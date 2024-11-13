from exponent_server_sdk import DeviceNotRegisteredError, PushClient, PushMessage, PushServerError, PushTicketError
from requests.exceptions import ConnectionError, HTTPError
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

        # Construct the push message
        push_message = PushMessage(
            to=tokens,
            body=message,
            data=data or {},
            sound="default",
            priority='high',
        )
        
        # Send the push message
        response = PushClient().publish(push_message)
        
        # Validate the response
        response.validate_response()
        
        # Log success
        logger.info(f"Push notification sent successfully to {len(tokens)} devices")
        return True
        
    except PushServerError as exc:
        # Handle server errors
        logger.error(f"Push server error: {exc.message}, Errors: {exc.errors}, Response data: {exc.response_data}")
        return False
        
    except (ConnectionError, HTTPError) as exc:
        # Handle connection and HTTP errors
        logger.error(f"Connection/HTTP error: {str(exc)}")
        return False
        
    except DeviceNotRegisteredError:
        # Handle unregistered devices
        for token in tokens:
            logger.warning(f"Device not registered: {token}")
            # Mark the device as inactive
            DeviceToken.objects.filter(token=token).update(active=False)
        return False
        
    except PushTicketError as exc:
        # Handle per-notification errors
        logger.error(f"Push ticket error: {exc.message}, Response: {exc.push_response}")
        return False
        
    except Exception as exc:
        # Handle any other errors
        logger.error(f"Unexpected error sending push notification: {str(exc)}")
        return False 