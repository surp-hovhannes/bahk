from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage, PushServerError, PushTicketError
from requests.exceptions import ConnectionError, HTTPError
from .models import DeviceToken
import logging
import re

logger = logging.getLogger(__name__)

def is_valid_expo_push_token(token):
    """Validate the format of an Expo push token."""
    pattern = r'^ExponentPushToken\[[a-zA-Z0-9_-]+\]$'
    return bool(re.match(pattern, token))

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

        # Initialize Expo client
        client = PushClient()
        
        # Create a list to store all messages
        messages = []
        
        # Create a message for each token
        for token in tokens:
            messages.append(
                PushMessage(
                    to=token,
                    body=message,
                    data=data or {},
                    sound="default",
                    priority='high',
                )
            )

        # Send all messages
        responses = []
        for msg in messages:
            try:
                response = client.publish(msg)
                responses.append(response)
                logger.info(f"Successfully sent to {msg.to}")
            except Exception as e:
                logger.error(f"Error sending to {msg.to}: {str(e)}")
                
        # Log success
        logger.info(f"Push notification sent to {len(responses)} devices")
        return True
        
    except PushServerError as exc:
        logger.error(f"Push server error: {str(exc)}")
        return False
        
    except (ConnectionError, HTTPError) as exc:
        logger.error(f"Connection/HTTP error: {str(exc)}")
        return False
        
    except DeviceNotRegisteredError:
        for token in tokens:
            logger.warning(f"Device not registered: {token}")
        return False
        
    except Exception as exc:
        logger.error(f"Unexpected error sending push notification: {str(exc)}")
        return False 

        send_push_notification(
    message="Hello from the console!",
    tokens=[token]
)