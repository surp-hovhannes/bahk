"""
Middleware to automatically track app open, session start/end, screen views,
and ingest UTM parameters for attribution.

This is lightweight and safe: it gracefully skips tracking if event types are
not initialized, and only runs for authenticated users.
"""

import uuid
from django.utils import timezone
from django.core.cache import cache
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings
from hub.utils import get_user_profile_safe


class AnalyticsTrackingMiddleware(MiddlewareMixin):
    """
    Tracks per-request analytics for authenticated users:
    - Starts a new session when inactivity exceeds configured threshold
    - Emits app_open and session_start when a session starts
    - Emits session_end for the previous session when a new session starts after inactivity
    - Emits screen_view for GET requests (uses X-Screen header or request path)
    - Ingests UTM parameters and stores them on the user's profile
    """

    def process_request(self, request):
        # For API requests, try to authenticate using JWT first
        user = self._get_authenticated_user(request)
        if not user or not user.is_authenticated:
            return None

        # Ingest UTM parameters into Profile if present
        self._ingest_utm_params(request, user)

        # Session management
        now = timezone.now()
        timeout_minutes = getattr(settings, 'ANALYTICS_SESSION_TIMEOUT_MINUTES', 30)
        inactivity_seconds = timeout_minutes * 60

        sess_key = f"analytics:session:{user.id}"
        last_seen_key = f"analytics:last_seen:{user.id}"

        session_data = cache.get(sess_key)
        last_seen = cache.get(last_seen_key)

        should_start_new_session = (
            session_data is None or last_seen is None or (now - last_seen).total_seconds() > inactivity_seconds
        )

        # If we're starting a new session and we have an old one, end it first
        if should_start_new_session and session_data and last_seen:
            try:
                from .models import Event, EventType
                duration_seconds = max(0, int((last_seen - session_data.get('start')).total_seconds())) if session_data.get('start') else 0
                Event.create_event(
                    event_type_code=EventType.SESSION_END,
                    user=user,
                    title='Session ended',
                    data={
                        'session_id': session_data.get('id'),
                        'duration_seconds': duration_seconds,
                        'requests': session_data.get('requests', 0),
                    },
                    request=request,
                )
            except Exception:
                # Skip on any error, including missing event types
                pass

        if should_start_new_session:
            # Start a fresh session
            new_session = {
                'id': str(uuid.uuid4()),
                'start': now,
                'requests': 0,
            }
            cache.set(sess_key, new_session, timeout=inactivity_seconds * 4)

            # Emit app_open and session_start
            try:
                from .models import Event, EventType
                base_data = {
                    'session_id': new_session['id'],
                    'path': request.path,
                    'app_version': request.META.get('HTTP_X_APP_VERSION'),
                    'platform': request.META.get('HTTP_X_PLATFORM'),
                }
                Event.create_event(
                    event_type_code=EventType.APP_OPEN,
                    user=user,
                    title='App opened',
                    data=base_data,
                    request=request,
                )
                Event.create_event(
                    event_type_code=EventType.SESSION_START,
                    user=user,
                    title='Session started',
                    data=base_data,
                    request=request,
                )
            except Exception:
                pass

            session_data = new_session

        # Update counters and last seen
        if session_data:
            session_data['requests'] = session_data.get('requests', 0) + 1
            cache.set(sess_key, session_data, timeout=inactivity_seconds * 4)

        cache.set(last_seen_key, now, timeout=inactivity_seconds * 4)

        # Screen view for GET requests
        if request.method == 'GET':
            screen_name = request.META.get('HTTP_X_SCREEN') or request.GET.get('screen')
            if not screen_name:
                # Fallback to path for automatic tracking
                screen_name = request.path
            try:
                from .models import Event, EventType
                Event.create_event(
                    event_type_code=EventType.SCREEN_VIEW,
                    user=user,
                    title=f"Screen view: {screen_name}",
                    data={
                        'session_id': session_data.get('id') if session_data else None,
                        'screen': screen_name,
                        'path': request.path,
                    },
                    request=request,
                )
            except Exception:
                pass

        return None

    def _get_authenticated_user(self, request):
        """
        Get authenticated user, handling both session and JWT authentication.
        """
        # First try the standard Django user (for admin/session auth)
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            return user
        
        # For API requests with JWT tokens, manually authenticate
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                from rest_framework_simplejwt.authentication import JWTAuthentication
                from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
                
                jwt_authenticator = JWTAuthentication()
                validated_token = jwt_authenticator.get_validated_token(token)
                user = jwt_authenticator.get_user(validated_token)
                
                if user and user.is_authenticated:
                    # Set the user on the request for consistency
                    request.user = user
                    return user
                    
            except (InvalidToken, TokenError, Exception):
                # Token is invalid or expired, skip tracking
                pass
        
        return None

    def _ingest_utm_params(self, request, user):
        params = getattr(request, 'query_params', None) or getattr(request, 'GET', {})
        utm_source = params.get('utm_source')
        utm_campaign = params.get('utm_campaign')
        join_source = params.get('join_source') or request.META.get('HTTP_X_JOIN_SOURCE')

        # Update profile if values provided and changed
        profile = get_user_profile_safe(user)
        if profile is None:
            # User has no profile, skip UTM ingestion
            return
        fields_to_update = []

        if utm_source is not None and utm_source != profile.utm_source:
            profile.utm_source = utm_source
            fields_to_update.append('utm_source')
        if utm_campaign is not None and utm_campaign != profile.utm_campaign:
            profile.utm_campaign = utm_campaign
            fields_to_update.append('utm_campaign')
        if join_source is not None and join_source != profile.join_source:
            profile.join_source = join_source
            fields_to_update.append('join_source')

        if fields_to_update:
            try:
                profile.save(update_fields=fields_to_update)
            except Exception:
                # Do not fail the request on attribution issues
                pass

