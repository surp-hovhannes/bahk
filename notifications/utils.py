from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage, PushServerError, PushTicketError
from requests.exceptions import ConnectionError, HTTPError
from .models import DeviceToken
import logging
import json
from datetime import datetime
from django.utils import timezone

logger = logging.getLogger(__name__)

def send_push_notification(message, data=None, tokens=None):
    """
    Send push notifications to specified tokens or all registered devices.
    
    Args:
        message (str): The notification message to send
        data (dict, optional): Additional data to send with the notification
        tokens (list, optional): List of specific tokens to send to. If None, sends to all registered tokens.
    
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
        # Validate and prepare tokens
        if tokens is None:
            tokens = DeviceToken.objects.filter(
                is_active=True
            ).values_list('token', flat=True)
        
        tokens = list(tokens)  # Convert QuerySet to list if necessary
        
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
                response = client.publish(
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