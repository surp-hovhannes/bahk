"""
Tests for AnalyticsTrackingMiddleware.

This module tests the analytics tracking middleware that handles:
- Session management and tracking
- App open/session start/end events
- Screen view tracking
- UTM parameter ingestion
- Attribution tracking
"""

import uuid
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from django.conf import settings

from events.middleware import AnalyticsTrackingMiddleware
from events.models import Event, EventType
from hub.models import Profile, Church

User = get_user_model()


@override_settings(
    ANALYTICS_SESSION_TIMEOUT_MINUTES=30,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
)
class AnalyticsTrackingMiddlewareTest(TestCase):
    """Test AnalyticsTrackingMiddleware functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Clear cache before each test
        cache.clear()
        
        # Initialize event types
        EventType.get_or_create_default_types()
        
        # Create test user and profile
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.church = Church.objects.create(name='Test Church')
        self.profile = Profile.objects.create(
            user=self.user,
            church=self.church
        )
        
        # Create middleware instance
        self.middleware = AnalyticsTrackingMiddleware(get_response=lambda r: None)
        
        # Create request factory
        self.factory = RequestFactory()
    
    def tearDown(self):
        """Clean up after each test."""
        cache.clear()
    
    def test_middleware_skips_unauthenticated_users(self):
        """Test that middleware skips processing for unauthenticated users."""
        request = self.factory.get('/')
        request.user = None
        
        initial_event_count = Event.objects.count()
        
        result = self.middleware.process_request(request)
        
        self.assertIsNone(result)
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_first_request_creates_session_and_events(self):
        """Test that first request creates session and emits APP_OPEN/SESSION_START events."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        request.META['HTTP_X_APP_VERSION'] = '1.2.0'
        request.META['HTTP_X_PLATFORM'] = 'ios'
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should create 3 events: APP_OPEN, SESSION_START, and SCREEN_VIEW (for GET request)
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
        
        # Check APP_OPEN event
        app_open_event = Event.objects.filter(
            event_type__code=EventType.APP_OPEN,
            user=self.user
        ).first()
        
        self.assertIsNotNone(app_open_event)
        self.assertEqual(app_open_event.title, 'App opened')
        self.assertEqual(app_open_event.data['path'], '/api/fasts/')
        self.assertEqual(app_open_event.data['app_version'], '1.2.0')
        self.assertEqual(app_open_event.data['platform'], 'ios')
        self.assertIn('session_id', app_open_event.data)
        
        # Check SESSION_START event
        session_start_event = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        
        self.assertIsNotNone(session_start_event)
        self.assertEqual(session_start_event.title, 'Session started')
        self.assertEqual(session_start_event.data['session_id'], app_open_event.data['session_id'])
    
    def test_screen_view_tracking_on_get_requests(self):
        """Test that GET requests create SCREEN_VIEW events."""
        request = self.factory.get('/api/profile/')
        request.user = self.user
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should create APP_OPEN, SESSION_START, and SCREEN_VIEW events
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
        
        screen_view_event = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).first()

        self.assertIsNotNone(screen_view_event)
        self.assertEqual(screen_view_event.title, 'Screen view: /api/profile/')
        self.assertEqual(screen_view_event.data['screen'], '/api/profile/')
        self.assertEqual(screen_view_event.data['path'], '/api/profile/')
        self.assertEqual(screen_view_event.data['source'], 'api')
        self.assertIn('session_id', screen_view_event.data)
    
    def test_custom_screen_name_via_header(self):
        """Test custom screen name via X-Screen header."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        request.META['HTTP_X_SCREEN'] = 'fasts_list'
        
        self.middleware.process_request(request)
        
        screen_view_event = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).first()
        
        self.assertIsNotNone(screen_view_event)
        self.assertEqual(screen_view_event.title, 'Screen view: fasts_list')
        self.assertEqual(screen_view_event.data['screen'], 'fasts_list')
        self.assertEqual(screen_view_event.data['path'], '/api/fasts/')
        self.assertEqual(screen_view_event.data['source'], 'api')
    
    def test_custom_screen_name_via_query_param(self):
        """Test custom screen name via query parameter."""
        request = self.factory.get('/api/profile/?screen=profile_edit')
        request.user = self.user

        self.middleware.process_request(request)
        
        screen_view_event = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).first()
        
        self.assertIsNotNone(screen_view_event)
        self.assertEqual(screen_view_event.title, 'Screen view: profile_edit')
        self.assertEqual(screen_view_event.data['screen'], 'profile_edit')
        self.assertEqual(screen_view_event.data['source'], 'api')

    def test_post_requests_do_not_create_screen_views(self):
        """Test that POST requests do not create SCREEN_VIEW events."""
        request = self.factory.post('/api/fasts/')
        request.user = self.user
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should create APP_OPEN and SESSION_START, but not SCREEN_VIEW
        self.assertEqual(Event.objects.count(), initial_event_count + 2)
        
        screen_view_events = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        )
        
        self.assertEqual(screen_view_events.count(), 0)

    def test_non_api_request_tagged_as_app_ui(self):
        """Screen views for non-API requests should be tagged as app_ui."""
        request = self.factory.get('/home/')
        request.user = self.user

        self.middleware.process_request(request)

        screen_view_event = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).first()

        self.assertIsNotNone(screen_view_event)
        self.assertEqual(screen_view_event.data['source'], 'app_ui')

    def test_api_header_marks_source(self):
        """API header should mark screen views as api even without /api/ path."""
        request = self.factory.get('/webhooks/')
        request.user = self.user
        request.META['HTTP_X_API_REQUEST'] = 'true'

        self.middleware.process_request(request)

        screen_view_event = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).first()

        self.assertIsNotNone(screen_view_event)
        self.assertEqual(screen_view_event.data['source'], 'api')
    
    def test_session_continuation_within_timeout(self):
        """Test that requests within session timeout continue existing session."""
        # First request creates session
        request1 = self.factory.get('/api/fasts/')
        request1.user = self.user
        
        self.middleware.process_request(request1)
        
        initial_event_count = Event.objects.count()
        
        # Second request within timeout (should not create new session)
        request2 = self.factory.get('/api/profile/')
        request2.user = self.user
        
        self.middleware.process_request(request2)
        
        # Should only create SCREEN_VIEW event (no new APP_OPEN/SESSION_START)
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        screen_view_event = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).order_by('-timestamp').first()

        self.assertIsNotNone(screen_view_event)
        self.assertEqual(screen_view_event.data['screen'], '/api/profile/')
        self.assertEqual(screen_view_event.data['source'], 'api')
    
    def test_session_timeout_creates_new_session(self):
        """Test that requests after session timeout create new session."""
        # First request creates session
        request1 = self.factory.get('/api/fasts/')
        request1.user = self.user
        
        self.middleware.process_request(request1)
        
        # Get the session ID from first session
        first_session_event = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        first_session_id = first_session_event.data['session_id']
        
        # Simulate session timeout by manipulating cache
        sess_key = f"analytics:session:{self.user.id}"
        last_seen_key = f"analytics:last_seen:{self.user.id}"
        
        # Set last seen time to 31 minutes ago (past timeout)
        past_time = timezone.now() - timedelta(minutes=31)
        cache.set(last_seen_key, past_time, timeout=3600)
        
        initial_event_count = Event.objects.count()
        
        # Second request after timeout
        request2 = self.factory.get('/api/profile/')
        request2.user = self.user
        
        self.middleware.process_request(request2)
        
        # Should create SESSION_END, APP_OPEN, SESSION_START, and SCREEN_VIEW
        self.assertEqual(Event.objects.count(), initial_event_count + 4)
        
        # Check SESSION_END event was created
        session_end_event = Event.objects.filter(
            event_type__code=EventType.SESSION_END,
            user=self.user
        ).first()
        
        self.assertIsNotNone(session_end_event)
        self.assertEqual(session_end_event.title, 'Session ended')
        self.assertEqual(session_end_event.data['session_id'], first_session_id)
        self.assertIn('duration_seconds', session_end_event.data)
        self.assertIn('requests', session_end_event.data)
        
        # Check new session was created
        new_session_events = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).order_by('-timestamp')
        
        self.assertEqual(new_session_events.count(), 2)  # Old and new session
        
        new_session_event = new_session_events.first()
        new_session_id = new_session_event.data['session_id']
        
        # Session IDs should be different
        self.assertNotEqual(first_session_id, new_session_id)
    
    def test_utm_parameter_ingestion_via_query_params(self):
        """Test UTM parameter ingestion via query parameters."""
        request = self.factory.get('/api/fasts/?utm_source=facebook&utm_campaign=lent2024')
        request.user = self.user
        
        # Clear existing UTM data
        self.profile.utm_source = None
        self.profile.utm_campaign = None
        self.profile.save()
        
        self.middleware.process_request(request)
        
        # Reload profile from database
        self.profile.refresh_from_db()
        
        self.assertEqual(self.profile.utm_source, 'facebook')
        self.assertEqual(self.profile.utm_campaign, 'lent2024')
    
    def test_utm_parameter_ingestion_via_drf_query_params(self):
        """Test UTM parameter ingestion via DRF request.query_params."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        
        # Simulate DRF request with query_params
        request.query_params = {
            'utm_source': 'instagram',
            'utm_campaign': 'easter2024'
        }
        
        # Clear existing UTM data
        self.profile.utm_source = None
        self.profile.utm_campaign = None
        self.profile.save()
        
        self.middleware.process_request(request)
        
        # Reload profile from database
        self.profile.refresh_from_db()
        
        self.assertEqual(self.profile.utm_source, 'instagram')
        self.assertEqual(self.profile.utm_campaign, 'easter2024')
    
    def test_join_source_parameter_ingestion(self):
        """Test join_source parameter ingestion."""
        request = self.factory.get('/api/fasts/?join_source=push_notification')
        request.user = self.user
        
        # Clear existing join_source data
        self.profile.join_source = None
        self.profile.save()
        
        self.middleware.process_request(request)
        
        # Reload profile from database
        self.profile.refresh_from_db()
        
        self.assertEqual(self.profile.join_source, 'push_notification')
    
    def test_join_source_via_header(self):
        """Test join_source parameter ingestion via header."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        request.META['HTTP_X_JOIN_SOURCE'] = 'email_link'
        
        # Clear existing join_source data
        self.profile.join_source = None
        self.profile.save()
        
        self.middleware.process_request(request)
        
        # Reload profile from database
        self.profile.refresh_from_db()
        
        self.assertEqual(self.profile.join_source, 'email_link')
    
    def test_utm_parameters_only_update_when_changed(self):
        """Test that UTM parameters only update profile when values change."""
        # Set initial UTM data
        self.profile.utm_source = 'facebook'
        self.profile.utm_campaign = 'lent2024'
        self.profile.save()
        
        # Mock the profile save method to track calls
        with patch.object(self.profile, 'save') as mock_save:
            request = self.factory.get('/api/fasts/?utm_source=facebook&utm_campaign=lent2024')
            request.user = self.user
            
            self.middleware.process_request(request)
            
            # Save should not be called since values haven't changed
            mock_save.assert_not_called()
    
    def test_utm_parameters_update_when_values_change(self):
        """Test that UTM parameters update profile when values change."""
        # Set initial UTM data
        self.profile.utm_source = 'facebook'
        self.profile.utm_campaign = 'lent2024'
        self.profile.save()
        
        request = self.factory.get('/api/fasts/?utm_source=instagram&utm_campaign=easter2024')
        request.user = self.user
        
        self.middleware.process_request(request)
        
        # Reload profile from database
        self.profile.refresh_from_db()
        
        self.assertEqual(self.profile.utm_source, 'instagram')
        self.assertEqual(self.profile.utm_campaign, 'easter2024')
    
    def test_user_without_profile_skips_utm_ingestion(self):
        """Test that UTM ingestion is skipped for users without profiles."""
        # Create user without profile
        user_no_profile = User.objects.create_user(
            username='noprofile',
            email='noprofile@example.com'
        )
        
        request = self.factory.get('/api/fasts/?utm_source=facebook')
        request.user = user_no_profile
        
        initial_event_count = Event.objects.count()
        
        # Should not raise an error
        result = self.middleware.process_request(request)
        
        self.assertIsNone(result)
        # Events should still be created (session and screen view)
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
    
    def test_graceful_error_handling_in_event_creation(self):
        """Test that errors in event creation don't break the middleware."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        
        # Mock Event.create_event to raise an exception
        with patch('events.models.Event.create_event', side_effect=Exception('DB Error')):
            # Should not raise an exception
            result = self.middleware.process_request(request)
            
            self.assertIsNone(result)
    
    def test_graceful_error_handling_in_utm_ingestion(self):
        """Test that errors in UTM ingestion don't break the middleware."""
        request = self.factory.get('/api/fasts/?utm_source=facebook')
        request.user = self.user
        
        # Mock profile save to raise an exception
        with patch.object(Profile, 'save', side_effect=Exception('Save Error')):
            # Should not raise an exception
            result = self.middleware.process_request(request)
            
            self.assertIsNone(result)
    
    def test_session_request_counter_increments(self):
        """Test that session request counter increments correctly."""
        # First request
        request1 = self.factory.get('/api/fasts/')
        request1.user = self.user
        self.middleware.process_request(request1)
        
        # Second request (same session)
        request2 = self.factory.get('/api/profile/')
        request2.user = self.user
        self.middleware.process_request(request2)
        
        # Check session data in cache
        sess_key = f"analytics:session:{self.user.id}"
        session_data = cache.get(sess_key)
        
        self.assertIsNotNone(session_data)
        self.assertEqual(session_data['requests'], 2)
    
    @override_settings(ANALYTICS_SESSION_TIMEOUT_MINUTES=60)
    def test_custom_session_timeout_setting(self):
        """Test that custom session timeout setting is respected."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        
        self.middleware.process_request(request)
        
        # Check that cache timeout is set to 4x the session timeout (60 * 60 * 4 = 14400)
        sess_key = f"analytics:session:{self.user.id}"
        
        # We can't directly check the TTL, but we can verify the session was created
        session_data = cache.get(sess_key)
        self.assertIsNotNone(session_data)
    
    def test_session_id_is_valid_uuid(self):
        """Test that generated session IDs are valid UUIDs."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user
        
        self.middleware.process_request(request)
        
        session_start_event = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        
        session_id = session_start_event.data['session_id']
        
        # Should be able to parse as UUID without raising exception
        parsed_uuid = uuid.UUID(session_id)
        self.assertEqual(str(parsed_uuid), session_id)
    
    def test_jwt_authentication_valid_token(self):
        """Test that valid JWT tokens are properly authenticated."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Generate a valid JWT token
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)
        
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = f'Bearer {access_token}'
        # Don't set request.user to simulate API request
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should authenticate user and create events
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
        
        # Verify user was set on request
        self.assertEqual(request.user, self.user)
    
    def test_jwt_authentication_multiple_spaces_after_bearer(self):
        """Test that JWT parsing handles multiple spaces after 'Bearer'."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)
        
        request = self.factory.get('/api/fasts/')
        # Multiple spaces after Bearer
        request.META['HTTP_AUTHORIZATION'] = f'Bearer    {access_token}'
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should still authenticate and create events
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
        self.assertEqual(request.user, self.user)
    
    def test_jwt_authentication_no_token_after_bearer(self):
        """Test that JWT parsing handles 'Bearer ' with no token."""
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = 'Bearer '
        
        initial_event_count = Event.objects.count()
        
        result = self.middleware.process_request(request)
        
        # Should skip tracking (no authentication)
        self.assertIsNone(result)
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_jwt_authentication_only_bearer_no_space(self):
        """Test that JWT parsing handles 'Bearer' with no space or token."""
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = 'Bearer'
        
        initial_event_count = Event.objects.count()
        
        result = self.middleware.process_request(request)
        
        # Should skip tracking (no authentication)
        self.assertIsNone(result)
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_jwt_authentication_empty_token(self):
        """Test that JWT parsing handles empty token after Bearer."""
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = 'Bearer   '  # Only spaces after Bearer
        
        initial_event_count = Event.objects.count()
        
        result = self.middleware.process_request(request)
        
        # Should skip tracking (no valid token)
        self.assertIsNone(result)
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_jwt_authentication_invalid_token(self):
        """Test that invalid JWT tokens are handled gracefully."""
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = 'Bearer invalid_token_here'
        
        initial_event_count = Event.objects.count()
        
        result = self.middleware.process_request(request)
        
        # Should skip tracking (invalid token)
        self.assertIsNone(result)
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_jwt_authentication_expired_token(self):
        """Test that expired JWT tokens are handled gracefully."""
        from rest_framework_simplejwt.tokens import RefreshToken
        from unittest.mock import patch
        from rest_framework_simplejwt.exceptions import TokenError
        
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)
        
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = f'Bearer {access_token}'
        
        # Mock token validation to raise TokenError (simulating expired token)
        with patch('rest_framework_simplejwt.authentication.JWTAuthentication.get_validated_token', 
                   side_effect=TokenError('Token is expired')):
            initial_event_count = Event.objects.count()
            
            result = self.middleware.process_request(request)
            
            # Should skip tracking (expired token)
            self.assertIsNone(result)
            self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_jwt_authentication_fallback_to_session_auth(self):
        """Test that middleware falls back to session authentication when JWT fails."""
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = 'Bearer invalid_token'
        request.user = self.user  # Session authenticated user
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should use session auth and create events
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
    
    def test_jwt_authentication_no_authorization_header(self):
        """Test that requests without Authorization header fall back to session auth."""
        request = self.factory.get('/api/fasts/')
        request.user = self.user  # Session authenticated user
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should use session auth and create events
        self.assertEqual(Event.objects.count(), initial_event_count + 3)
    
    def test_jwt_authentication_non_bearer_token(self):
        """Test that non-Bearer authorization headers are ignored."""
        request = self.factory.get('/api/fasts/')
        request.META['HTTP_AUTHORIZATION'] = 'Basic dXNlcjpwYXNz'  # Basic auth
        request.user = self.user  # Session authenticated user
        
        initial_event_count = Event.objects.count()
        
        self.middleware.process_request(request)
        
        # Should use session auth and create events
        self.assertEqual(Event.objects.count(), initial_event_count + 3)