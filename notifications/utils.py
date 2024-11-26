from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage, PushServerError, PushTicketError
from requests.exceptions import ConnectionError, HTTPError
from .models import DeviceToken
import logging
import json
import re
from datetime import datetime
from django.utils import timezone
from django.db.models import Q
from .constants import NOTIFICATION_TYPE_FILTERS, WEEKLY_FAST_NAMES

logger = logging.getLogger(__name__)


def is_weekly_fast(fast):
    """Checks if fast is weekly (e.g., Wednesday or Friday fast) or not.
    
    Args:
        fast (models.Fast): fast to check if weekly

    Results:
        bool: True if fast is weekly; else False
    """
    return bool(re.match("wednesday|friday", fast.name, re.I))


def send_push_notification(message, data=None, users=None, notification_type=None):
    """
    Send push notifications to specified tokens or all registered devices.
    
    Args:
        message (str): The notification message to send
        data (dict, optional): Additional data to send with the notification
        users (list, optional): List of specific users to send to. If None, sends to all registered users.
        notification_type (str, optional): Type of notification. If not specified, will send to all users regardless of
            preferences. Defaults to None. Other options: ('upcoming_fast', 'ongoing_fast', 'daily_fast', 'weekly_fast')
    
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
        logger.info(f"Starting push notification with users: {users or 'all'}")
        
        # Get all active tokens for the specified users
        tokens_queryset = DeviceToken.objects.filter(is_active=True)
        
        if users:
            tokens_queryset = tokens_queryset.filter(user__in=users)

        if notification_type in NOTIFICATION_TYPE_FILTERS:
            filter_field = NOTIFICATION_TYPE_FILTERS[notification_type]
            tokens_queryset = tokens_queryset.filter(**{f'user__profile__{filter_field}': True})

        tokens = list(tokens_queryset.values_list('token', flat=True))

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
        for token in tokens:
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