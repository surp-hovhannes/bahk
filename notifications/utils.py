from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage, PushServerError
from .models import DeviceToken
import hashlib
import secrets
import logging
import json
import re
import time
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


def _deactivate_dead_token(token, result):
    """Share reversible dead-device handling for returned tickets and exceptions."""
    with transaction.atomic():
        DeviceToken.objects.filter(token=token).update(is_active=False)
    result['failed'] += 1
    token_id = _device_token_log_id(token)
    result['invalid_tokens'].append(token_id)
    logger.warning("Device not registered: %s", token_id)


EXPO_ERROR_CODES = frozenset({
    'DeviceNotRegistered', 'MessageTooBig', 'MessageRateExceeded',
    'MismatchSenderId', 'InvalidCredentials', 'DeveloperError',
})


# Only a structured whole-request rejection proves Expo did not enqueue this send.
# Bare HTTP failures (including 503), timeouts and malformed replies may follow
# acceptance. Never replay those. Keep retries inside the single-device boundary.
PUSH_RETRY_DELAYS = (1, 2)
# A call-wide budget (not reset by successes) bounds outage amplification.
PUSH_TRANSIENT_LIMIT = 3
TRANSPORT_ERROR_LIMIT = 20
_SKIP_SEND = object()


def _transport_failure(error):
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    status = status if type(status) is int and 100 <= status <= 599 else None
    structured = (
        isinstance(error, PushServerError)
        and isinstance(error.response_data, dict)
        and isinstance(error.response_data.get('errors'), list)
        and bool(error.response_data['errors'])
        and all(isinstance(item, dict) for item in error.response_data['errors'])
        and 'data' not in error.response_data
    )
    transient = status == 429 or (status is not None and 500 <= status <= 599)
    if isinstance(error, PushServerError) and isinstance(error.response_data, dict) and 'data' in error.response_data:
        classification = 'ambiguous_acceptance'
    elif structured and transient:
        classification = 'transient_reject'
    elif status is not None and 400 <= status < 500 and status not in {408, 429}:
        classification = 'permanent_reject'
    elif structured and status == 200:
        classification = 'permanent_reject'
    else:
        classification = 'ambiguous_acceptance'
    return {'operation': 'publish', 'classification': classification, 'http_status': status}


def _publish_with_recovery(client, message, result, eligible, recovery):
    for attempt in range(len(PUSH_RETRY_DELAYS) + 1):
        try:
            return client.publish(message)
        except Exception as error:
            if isinstance(error, DeviceNotRegisteredError) or getattr(error, 'push_response', None) is not None:
                raise
            failure = dict(_transport_failure(error), attempt=attempt + 1)
            if len(result['transport_errors']) < TRANSPORT_ERROR_LIMIT:
                result['transport_errors'].append(failure)
            else:
                result['transport_errors_omitted'] += 1
            if failure['classification'] == 'transient_reject':
                recovery['transient_rejections'] += 1
                if not recovery['circuit_open'] and recovery['transient_rejections'] >= PUSH_TRANSIENT_LIMIT:
                    recovery['circuit_open'] = True
                    logger.error(
                        'Push retry circuit open: retry budget exhausted; later eligible devices get one attempt. '
                        'Check Expo service health and rate limits; do not replay ambiguous or accepted sends.'
                    )
            logger.warning('Push transport operation=%s classification=%s status=%s attempt=%s',
                           failure['operation'], failure['classification'], failure['http_status'], failure['attempt'])
            if (failure['classification'] != 'transient_reject'
                    or recovery['circuit_open'] or attempt == len(PUSH_RETRY_DELAYS)):
                raise
            recovery['backed_off'] = True
            time.sleep(PUSH_RETRY_DELAYS[attempt])
            # Consent or token activity can change during backoff.
            if not eligible():
                return _SKIP_SEND


def _still_eligible(device, notification_type):
    # The materialized audience is fixed. Never repeat its potentially huge IN
    # clause; only mutable identity, activity and consent need refreshing.
    with transaction.atomic():
        query = DeviceToken.objects.filter(
            pk=device.pk, token=device.token, user_id=device.user_id, is_active=True,
        )
        preference = NOTIFICATION_TYPE_FILTERS.get(notification_type)
        if preference:
            query = query.filter(**{f'user__profile__{preference}': True})
        return query.exists()


def notification_destination(data):
    """Reduce outgoing navigation to a fixed family; never retain resource IDs."""
    if not isinstance(data, dict) or not data:
        return 'none'
    screen = data.get('screen')
    if screen is not None:
        if not isinstance(screen, str):
            return 'other'
        from events.event_schema import ROUTE_FAMILIES

        family, _, tail = screen.partition('/')
        pattern = ROUTE_FAMILIES.get(family)
        if pattern is not None and re.fullmatch(pattern, tail):
            return family.replace('-', '_')
        return 'other'
    if 'url' in data:
        return 'url'
    for key, family in (
        ('devotional_id', 'devotional'), ('prayer_set_id', 'prayer_set'),
        ('prayer_request_id', 'prayer_request'), ('fast_id', 'fast'),
    ):
        if key in data:
            return family
    return 'other'


def telemetry_task_kwargs(kind):
    """Old workers cannot accept kind during a flag-off rolling deploy."""
    return {'kind': kind} if getattr(settings, 'NOTIFICATION_TELEMETRY_ENABLED', False) else {}


def _ticket_field(ticket, name):
    return ticket.get(name) if isinstance(ticket, dict) else getattr(ticket, name, None)


def _valid_ticket_id(value):
    # Expo's opaque IDs use this bounded alphabet. Ignore unexpected shapes.
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None


def _expo_error_code(details):
    """Bounded Expo error vocabulary for ticket and receipt details."""
    code = details.get('error') if isinstance(details, dict) else None
    return code if isinstance(code, str) and code in EXPO_ERROR_CODES else 'other'


def _prepare_telemetry(data, message, kind, destination, device):
    telemetry = {
        'source': 'server_domain', 'notif_id': secrets.token_urlsafe(12),
        'kind': kind, 'destination': destination,
        'platform': device.device_type if device.device_type in {'ios', 'android', 'web'} else 'unknown',
        'outcome': 'send_error', 'correlatable': False,
    }
    try:
        augmented = dict(data or {}, fp_nid=telemetry['notif_id'], fp_kind=kind)
        # Leave headroom for provider-side keys.
        if len(json.dumps({'body': message, 'data': augmented}).encode('utf-8')) <= 4096 - 512:
            telemetry['correlatable'] = True
            return augmented, telemetry
    except Exception:
        logger.warning('Push telemetry preparation skipped')
    return data, telemetry


def _record_send(telemetry, ticket, error):
    if ticket is None and error is not None:
        ticket = getattr(error, 'push_response', None)
    # Read ticket fields directly; other provider rejections retain legacy counts.
    if ticket is not None:
        status = _ticket_field(ticket, 'status')
        if status == 'ok':
            telemetry['outcome'] = 'provider_accepted'
        elif status == 'error':
            telemetry['outcome'] = 'provider_rejected'
            telemetry.setdefault('error_code', _expo_error_code(_ticket_field(ticket, 'details')))
        ticket_id = _ticket_field(ticket, 'id')
        if _valid_ticket_id(ticket_id):
            telemetry['ticket_id'] = ticket_id


def send_push_notification(
    message, data=None, users=None, notification_type=None, device_token_ids=None, *, kind="unclassified",
):
    """
    Send push notifications to specified tokens or all registered devices.

    Sent counts mean non-raising publishes except DeviceNotRegistered, never delivery.
    Other ticket rejections retain their legacy sent counts. Skipped counts
    revoked eligibility, without send telemetry.
    Accepted publishes remain sent even when local bookkeeping fails.
    
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
        'skipped': 0,
        'circuit_open': False,
        'transport_errors_omitted': 0,
        'invalid_tokens': [],
        'errors': [],
        'transport_errors': []
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

        tokens = list(tokens_queryset.only('id', 'token', 'device_type', 'user_id'))

        if not tokens:
            logger.warning("No device tokens available for push notification")
            result['errors'].append("No device tokens available")
            return result

        # Initialize Expo client
        client = PushClient(timeout=10)
        # receipts imports this module's helpers, so import it lazily.
        from .receipts import ledger_entry, remember_tickets

        # Validate data parameter
        if data and not isinstance(data, dict):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                logger.error("Invalid data format provided")
                data = {}

        event_type = None
        if getattr(settings, 'NOTIFICATION_TELEMETRY_ENABLED', False):
            from events.event_schema import EVENT_SCHEMA_VERSION, NOTIFICATION_KINDS
            from events.models import Event, EventType

            if kind == 'unclassified' and notification_type in NOTIFICATION_KINDS:
                kind = notification_type
            kind = kind if isinstance(kind, str) and kind in NOTIFICATION_KINDS else 'unclassified'
            try:
                # Roll back lookup failures before continuing a caller's transaction.
                with transaction.atomic():
                    event_type = EventType.objects.filter(code=EventType.NOTIFICATION_SEND, is_active=True).first()
                if event_type is None:
                    logger.warning('Push telemetry type unavailable')
            except Exception:
                logger.warning('Push telemetry type unavailable')
        destination = notification_destination(data) if event_type else None
        telemetry_rows = []
        ledger_entries = []

        recovery = {'backed_off': False, 'transient_rejections': 0, 'circuit_open': False}

        # Send notifications
        for device in tokens:
            token = device.token
            payload, telemetry = data, None
            ticket = None
            error = None
            accepted = False
            try:
                if recovery['backed_off'] and not _still_eligible(device, notification_type):
                    result['skipped'] += 1
                    continue
                if event_type is not None:
                    try:
                        payload, telemetry = _prepare_telemetry(data, message, kind, destination, device)
                    except Exception:
                        logger.warning('Push telemetry preparation skipped')
                ticket = _publish_with_recovery(
                    client, PushMessage(
                        to=token,
                        body=message,
                        data=payload,
                        sound="default",
                        priority='high',
                    ), result,
                    lambda: _still_eligible(device, notification_type), recovery,
                )
                
                if ticket is _SKIP_SEND:
                    ticket, telemetry = None, None
                    result['skipped'] += 1
                    continue

                if (
                    _ticket_field(ticket, 'status') == 'error'
                    and _expo_error_code(_ticket_field(ticket, 'details')) == 'DeviceNotRegistered'
                ):
                    _deactivate_dead_token(token, result)
                    continue

                # Acceptance survives local bookkeeping failure. A savepoint
                # prevents a DB error from poisoning the caller's transaction.
                accepted = True
                result['sent'] += 1
                with transaction.atomic():
                    DeviceToken.objects.filter(token=token).update(last_used=timezone.now())
                logger.info("Sent to push provider: %s", _device_token_log_id(token))

            except DeviceNotRegisteredError as exc:
                error = exc
                if telemetry is not None:
                    telemetry.update(outcome="provider_rejected", error_code="DeviceNotRegistered")
                try:
                    _deactivate_dead_token(token, result)
                except Exception as cleanup_error:
                    result['failed'] += 1
                    if len(result['errors']) < TRANSPORT_ERROR_LIMIT:
                        result['errors'].append(type(cleanup_error).__name__)
                    logger.warning('Push dead-token cleanup unavailable')

            except Exception as e:
                error = e
                if accepted:
                    logger.error('Push accepted but local bookkeeping failed: %s (%s)',
                                 _device_token_log_id(token), type(e).__name__)
                else:
                    logger.error('Push send failed: %s (%s)', _device_token_log_id(token), type(e).__name__)
                if not accepted:
                    result['failed'] += 1
                if len(result['errors']) < TRANSPORT_ERROR_LIMIT:
                    result['errors'].append(type(e).__name__)
            finally:
                entry = ledger_entry(ticket, device.pk, telemetry)
                if entry is not None:
                    ledger_entries.append(entry)
                if telemetry is not None:
                    try:
                        _record_send(telemetry, ticket, error)
                        telemetry_rows.append(Event(
                            event_type=event_type, user_id=device.user_id,
                            title=EventType.NOTIFICATION_LIFECYCLE_TITLES[EventType.NOTIFICATION_SEND],
                            description='', ip_address=None, user_agent='',
                            data={**telemetry, 'schema_version': EVENT_SCHEMA_VERSION},
                        ))
                    except Exception:
                        logger.warning('Push telemetry outcome skipped')

        result['circuit_open'] = recovery['circuit_open']
        # One pipelined Redis write per send, not a blocking round trip per device.
        remember_tickets(ledger_entries)
        if telemetry_rows:
            try:
                with transaction.atomic():
                    Event.objects.bulk_create(telemetry_rows, batch_size=500)
            except Exception:
                logger.warning('Push telemetry write skipped')

        result['success'] = result['sent'] > 0
        return result

    except Exception as exc:
        logger.error('Unexpected push send failure: %s', type(exc).__name__)
        result['errors'].append(type(exc).__name__)
        return result 
