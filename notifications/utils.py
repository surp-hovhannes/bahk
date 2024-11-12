from exponent_server_sdk import PushClient, PushMessage, PushServerError, PushResponseError
from .models import DeviceToken
import logging

logger = logging.getLogger(__name__)

def send_push_notification(message, data=None, tokens=None):
    """
    Send push notifications to specified tokens or all registered devices.
    
    Args:
        message (str): The notification message to send
        data (dict, optional): Additional data to send with the notification
        tokens (list, optional): List of specific tokens to send to. If None, sends to all registered tokens.
    
    Returns:
        bool: True if all notifications were sent successfully, False otherwise
    """
    try:
        # If no specific tokens provided, get all registered tokens
        if tokens is None:
            tokens = DeviceToken.objects.values_list('token', flat=True)
            tokens = list(tokens)  # Convert QuerySet to list
        
        if not tokens:
            logger.warning("No device tokens available for push notification")
            return False

        # Initialize Expo SDK client
        client = PushClient()
        
        # Send notifications
        response = client.publish(
            PushMessage(
                to=tokens,
                body=message,
                data=data or {},
                sound="default",
                priority='high',
            )
        )
        
        # Log success
        logger.info(f"Push notification sent successfully to {len(tokens)} devices")
        return True
        
    except PushServerError as e:
        # Handle server errors from Expo
        logger.error(f"Push server error: {str(e)}")
        return False
    except PushResponseError as e:
        # Handle response errors
        logger.error(f"Push response error: {str(e)}")
        return False
    except Exception as e:
        # Handle any other unexpected errors
        logger.error(f"Unexpected error sending push notification: {str(e)}")
        return False 