from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage
from .models import DeviceToken
import hashlib
import secrets
import logging
import json
import re
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .constants import NOTIFICATION_TYPE_FILTERS

logger = logging.getLogger(__name__)


def is_weekly_fast(fast):
    """Checks if fast is weekly (e.g., Wednesday or Friday fast) or not.
    
    Args:
        fast (models.Fast): fast to check if weekly

    Results:
        bool: True if fast is weekly; else False
    """
    return bool(re.match("wednesday|friday", fast.name, re.I))


def _device_token_log_id(token_value):
    """Stable token identifier for logs without exposing the push token."""
    return hashlib.sha256(token_value.encode('utf-8')).hexdigest()[:12]


EXPO_ERROR_CODES = frozenset({
    'DeviceNotRegistered', 'MessageTooBig', 'MessageRateExceeded',
    'MismatchSenderId', 'InvalidCredentials', 'DeveloperError',
})


def notification_destination(data):
    """Reduce outgoing navigation to a fixed family; never retain resource IDs."""
    if not isinstance(data, dict) or not data:
        return 'none'
    screen = data.get('screen')
    if screen is not None:
        if not isinstance(screen, str):
            return 'other'
        if screen in {'prayer-requests', 'learn'}:
            return {'prayer-requests': 'prayer_requests', 'learn': 'learn'}[screen]
        for route, family in (
            ('fast', 'fast'), ('devotional', 'devotional'),
            ('prayer-set', 'prayer_set'), ('prayer-request', 'prayer_request'),
        ):
            if screen == route or re.fullmatch(route + r'/[0-9]+', screen):
                return family
        return 'other'
    for key, family in (
        ('devotional_id', 'devotional'), ('prayer_set_id', 'prayer_set'),
        ('prayer_request_id', 'prayer_request'), ('fast_id', 'fast'),
    ):
        if key in data:
            return family
    return 'other'


def _prepare_telemetry(data, message, kind, device):
    payload = data or {}
    if not getattr(settings, 'NOTIFICATION_TELEMETRY_ENABLED', False):
        return payload, None
    try:
        from events.event_schema import NOTIFICATION_KINDS

        kind = kind if isinstance(kind, str) and kind in NOTIFICATION_KINDS else 'unclassified'
        telemetry = {
            'source': 'server_domain', 'notif_id': secrets.token_urlsafe(12),
            'kind': kind, 'destination': notification_destination(payload),
            'platform': device.device_type if device.device_type in {'ios', 'android', 'web'} else 'unknown',
            'outcome': 'send_error',
        }
        payload = dict(payload)
        augmented = dict(payload, fp_nid=telemetry['notif_id'], fp_kind=kind)
        # Conservative UTF-8 JSON envelope size, including Expo's normal defaults.
        # Near the 4096-byte limit preserve the original send rather than cause a
        # new MessageTooBig failure. Such attempts have no client correlation.
        envelope = dict(
            to=device.token, body=message, data=augmented,
            sound='default', priority='high', _displayInForeground=True,
        )
        if len(json.dumps(envelope).encode('utf-8')) <= 4096:
            payload = augmented
        return payload, telemetry
    except Exception:
        logger.warning('Push telemetry preparation skipped: %s', _device_token_log_id(device.token))
        return payload, None


def _record_send(device, telemetry, ticket, error):
    if telemetry is None:
        return
    try:
        from events.models import Event, EventType

        if ticket is None and error is not None:
            ticket = getattr(error, 'push_response', None)
        # SDK versions are unpinned. Read only explicit status/id/details fields;
        # never validate_response(), which would change legacy token/count behavior.
        if ticket is not None:
            status = ticket.get('status') if isinstance(ticket, dict) else getattr(ticket, 'status', None)
            if status == 'ok':
                telemetry['outcome'] = 'provider_accepted'
            elif status == 'error':
                telemetry['outcome'] = 'provider_rejected'
                details = ticket.get('details') if isinstance(ticket, dict) else getattr(ticket, 'details', None)
                code = details.get('error') if isinstance(details, dict) else None
                telemetry['error_code'] = code if isinstance(code, str) and code in EXPO_ERROR_CODES else 'other'
            ticket_id = ticket.get('id') if isinstance(ticket, dict) else getattr(ticket, 'id', None)
            # Expo's opaque IDs use this bounded alphabet. Ignore unexpected shapes.
            if isinstance(ticket_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ticket_id):
                telemetry['ticket_id'] = ticket_id
        # A savepoint keeps a failed insert from poisoning a caller's transaction.
        with transaction.atomic():
            Event.create_event(
                event_type_code=EventType.NOTIFICATION_SEND, user=device.user,
                title='Notification send', description='', data=telemetry,
            )
    except Exception:
        # No exception text: SDK/DB errors may contain private payloads or tokens.
        logger.warning('Push telemetry skipped: %s', _device_token_log_id(device.token))


def send_push_notification(
    message, data=None, users=None, notification_type=None, device_token_ids=None, *, kind="unclassified",
):
    """
    Send push notifications to specified tokens or all registered devices.

    Legacy sent counts mean non-raising publishes to the provider, never delivery.
    Ticket rejections are recorded separately without changing these counts.
    
    Args:
        message (str): The notification message to send
        data (dict, optional): Additional data to send with the notification
        users (list, optional): List of specific users to send to. If None, sends to all registered users.
        notification_type (str, optional): Type of notification. If not specified, will send to all users regardless of
            preferences. Defaults to None. Other options: ('upcoming_fast', 'ongoing_fast', 'daily_fast', 'weekly_fast')
        device_token_ids (list, optional): List of specific DeviceToken IDs to send to.
        kind (str, optional): Telemetry kind; independent of preference filtering.
    
    Returns:
        dict: Contains success status and details about sent/failed notifications
    """
    result = {
        'success': False,
        'sent': 0,
        'failed': 0,
        'invalid_tokens': [],
        'errors': []
    }

    try:
        logger.info("Starting push notification")
        
        # Get all active tokens for the specified users
        tokens_queryset = DeviceToken.objects.filter(is_active=True)
        
        if device_token_ids:
            tokens_queryset = tokens_queryset.filter(id__in=device_token_ids)
        elif users:
            tokens_queryset = tokens_queryset.filter(user__in=users)

        if notification_type in NOTIFICATION_TYPE_FILTERS:
            filter_field = NOTIFICATION_TYPE_FILTERS[notification_type]
            tokens_queryset = tokens_queryset.filter(**{f'user__profile__{filter_field}': True})

        tokens = list(tokens_queryset.select_related('user'))

        if not tokens:
            logger.warning("No device tokens available for push notification")
            result['errors'].append("No device tokens available")
            return result

        # Initialize Expo client
        client = PushClient()
        
        # Validate data parameter
        if data and not isinstance(data, dict):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                logger.error("Invalid data format provided")
                data = {}

        # Send notifications
        for device in tokens:
            token = device.token
            payload, telemetry = _prepare_telemetry(data, message, kind, device)
            ticket = None
            error = None
            try:
                ticket = client.publish(
                    PushMessage(
                        to=token,
                        body=message,
                        data=payload,
                        sound="default",
                        priority='high',
                    )
                )
                
                # Update device token last_used timestamp
                DeviceToken.objects.filter(token=token).update(
                    last_used=timezone.now()
                )
                
                result['sent'] += 1
                logger.info("Sent to push provider: %s", _device_token_log_id(token))
                
            except DeviceNotRegisteredError as exc:
                error = exc
                logger.warning("Device not registered: %s", _device_token_log_id(token))
                if telemetry is not None:
                    telemetry.update(outcome="provider_rejected", error_code="DeviceNotRegistered")
                DeviceToken.objects.filter(token=token).update(is_active=False)
                result['failed'] += 1
                result['invalid_tokens'].append(token)
                
            except Exception as e:
                error = e
                logger.error("Push send failed: %s", _device_token_log_id(token))
                result['failed'] += 1
                result['errors'].append(str(e))
            finally:
                _record_send(device, telemetry, ticket, error)

        result['success'] = result['sent'] > 0
        return result

    except Exception as exc:
        logger.error("Unexpected push send failure")
        result['errors'].append(str(exc))
        return result 
