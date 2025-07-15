"""
Middleware for handling timezone updates based on API request timezone information.
"""
import pytz
import logging
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class TimezoneUpdateMiddleware(MiddlewareMixin):
    """
    Middleware to automatically update user timezone when API requests include
    timezone information that differs from the user's current timezone.
    
    This middleware:
    1. Checks for 'tz' parameter in query parameters or 'X-Timezone' header
    2. Validates the timezone string 
    3. Updates the user's profile timezone if it differs from current
    4. Only updates for authenticated users with profiles
    """
    
    def process_request(self, request):
        """
        Process incoming request to check and update user timezone if needed.
        """
        # Only process for authenticated users
        if not request.user or not request.user.is_authenticated:
            return None
            
        # Check if user has a profile
        if not hasattr(request.user, 'profile'):
            return None
            
        # Get timezone from query parameter or header
        timezone_str = None
        
        # First check query parameters (from TimezoneMixin pattern)
        if hasattr(request, 'query_params'):
            timezone_str = request.query_params.get('tz')
        elif request.method == 'GET':
            timezone_str = request.GET.get('tz')
            
        # If not in query params, check headers
        if not timezone_str:
            timezone_str = request.META.get('HTTP_X_TIMEZONE')
            
        # If no timezone provided, nothing to update
        if not timezone_str:
            return None
            
        try:
            # Validate timezone string
            tz = pytz.timezone(timezone_str)
            
            # Get user's current timezone from profile
            current_timezone = request.user.profile.timezone
            
            # If timezone differs from current, update it
            if current_timezone != timezone_str:
                logger.info(f"Updating timezone for user {request.user.id} from {current_timezone} to {timezone_str}")
                
                # Update profile timezone
                request.user.profile.timezone = timezone_str
                request.user.profile.save(update_fields=['timezone'])
                
        except pytz.exceptions.UnknownTimeZoneError:
            # Invalid timezone string, log warning but don't fail the request
            logger.warning(f"Invalid timezone '{timezone_str}' provided by user {request.user.id}")
        except Exception as e:
            # Log any other errors but don't fail the request
            logger.error(f"Error updating timezone for user {request.user.id}: {e}")
            
        return None