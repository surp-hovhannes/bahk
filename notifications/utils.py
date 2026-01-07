from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage
from .models import DeviceToken
import logging
import json
import re
from django.utils import timezone
from django.db.models import Q
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


def send_push_notification(
    message,
    data=None,
    users=None,
    notification_type=None,
    tokens=None,
    token_ids=None,
):
    """
    Send push notifications to specified tokens or all registered devices.
    
    Args:
        message (str): The notification message to send
        data (dict, optional): Additional data to send with the notification
        users (list, optional): List of specific users to send to. If None, sends to all registered users.
        notification_type (str, optional): Type of notification. If not specified, will send to all users regardless of
            preferences. Defaults to None. Other options: ('upcoming_fast', 'ongoing_fast', 'daily_fast', 'weekly_fast')
        tokens (list, optional): Explicit list of Expo push tokens to target. When provided, users/notification_type are ignored.
        token_ids (list, optional): List of DeviceToken IDs to target. Useful for admin-selected batches.
    
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
        logger.info(
            "Starting push notification with users=%s token_ids=%s tokens_provided=%s",
            users or 'all',
            token_ids if token_ids is not None else 'none',
            bool(tokens),
        )

        # Get tokens to send to, preferring explicit tokens/token_ids when provided
        tokens_queryset = DeviceToken.objects.filter(is_active=True)

        if tokens is None:
            if token_ids:
                tokens_queryset = tokens_queryset.filter(id__in=token_ids)

            if users:
                tokens_queryset = tokens_queryset.filter(user__in=users)

            if notification_type in NOTIFICATION_TYPE_FILTERS:
                filter_field = NOTIFICATION_TYPE_FILTERS[notification_type]
                tokens_queryset = tokens_queryset.filter(**{f'user__profile__{filter_field}': True})

            tokens_to_send = list(tokens_queryset.values_list('token', flat=True))
        else:
            # Ensure provided tokens are unique and non-empty
            tokens_to_send = [t for t in tokens if t]
            tokens_to_send = list(dict.fromkeys(tokens_to_send))  # preserve order while de-duping

        if not tokens_to_send:
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
        for token in tokens_to_send:
            try:
                client.publish(
                    PushMessage(
                        to=token,
                        body=message,
                        data=data or {},
                        sound="default",
                        priority='high',
                    )
                )
                
                # Update device token last_used timestamp
                DeviceToken.objects.filter(token=token).update(
                    last_used=timezone.now()
                )
                
                result['sent'] += 1
                logger.info(f"Successfully sent to {token}")
                
            except DeviceNotRegisteredError:
                logger.warning(f"Device not registered: {token}")
                DeviceToken.objects.filter(token=token).update(is_active=False)
                result['failed'] += 1
                result['invalid_tokens'].append(token)
                
            except Exception as e:
                logger.error(f"Error sending to {token}: {str(e)}")
                result['failed'] += 1
                result['errors'].append(str(e))

        result['success'] = result['sent'] > 0
        return result

    except Exception as exc:
        logger.error(f"Unexpected error in send_push_notification: {str(exc)}")
        result['errors'].append(str(exc))
        return result 