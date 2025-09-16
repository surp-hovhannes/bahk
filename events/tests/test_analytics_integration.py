"""
Integration tests for analytics tracking system.

This module tests the complete analytics flow including:
- End-to-end user session tracking
- Attribution tracking from UTM to events
- Integration between middleware and endpoints
- Complete user journey analytics
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from events.middleware import AnalyticsTrackingMiddleware
from events.models import Event, EventType
from hub.models import Profile, Church, Fast, Day, Devotional, Video

User = get_user_model()


@override_settings(
    ANALYTICS_SESSION_TIMEOUT_MINUTES=30,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
)
class AnalyticsIntegrationTest(APITestCase):
    """Integration tests for complete analytics tracking."""
    
    def setUp(self):
        """Set up test data."""
        # Clear cache
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
        
        # Create test fast and devotional
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        self.day = Day.objects.create(
            fast=self.fast,
            date='2024-03-01',
            church=self.church
        )
        
        self.video = Video.objects.create(
            title='Test Devotional Video',
            description='Test devotional description',
            category='devotional'
        )
        
        self.devotional = Devotional.objects.create(
            day=self.day,
            video=self.video,
            description='Test devotional',
            order=1
        )
        
        # Set up client
        self.client = APIClient()
        
        # Create middleware instance for direct testing
        self.middleware = AnalyticsTrackingMiddleware(get_response=lambda r: None)
        self.factory = RequestFactory()
    
    def tearDown(self):
        """Clean up after each test."""
        cache.clear()
    
    def test_complete_user_session_flow(self):
        """Test complete user session from login to logout with activities."""
        initial_event_count = Event.objects.count()
        
        # Step 1: User logs in via JWT
        login_url = reverse('token_obtain_pair')
        login_data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        login_response = self.client.post(login_url, login_data, format='json')
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        
        # Set authentication for subsequent requests
        access_token = login_response.data['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        
        # Step 2: User makes first API request (creates session + screen view)
        # Simulate this by calling middleware directly with proper headers
        request = self.factory.get('/api/fasts/', HTTP_X_SCREEN='fasts_list')
        request.user = self.user
        
        self.middleware.process_request(request)
        
        # Step 3: User views a devotional
        devotional_url = reverse('events:track-devotional-viewed')
        devotional_data = {'devotional_id': self.devotional.id}
        
        devotional_response = self.client.post(devotional_url, devotional_data, format='json')
        self.assertEqual(devotional_response.status_code, status.HTTP_200_OK)
        
        # Step 4: User uses checklist
        checklist_url = reverse('events:track-checklist-used')
        checklist_data = {'fast_id': self.fast.id, 'action': 'morning_review'}
        
        checklist_response = self.client.post(checklist_url, checklist_data, format='json')
        self.assertEqual(checklist_response.status_code, status.HTTP_200_OK)
        
        # Step 5: User makes another screen view
        request2 = self.factory.get('/api/profile/', HTTP_X_SCREEN='profile_view')
        request2.user = self.user
        
        self.middleware.process_request(request2)
        
        # Verify all events were created
        total_events_created = Event.objects.count() - initial_event_count
        
        # Expected events:
        # 1. USER_LOGGED_IN (from JWT login)
        # 2. APP_OPEN (from first middleware request)
        # 3. SESSION_START (from first middleware request)
        # 4. SCREEN_VIEW (from first middleware request - fasts_list)
        # 5. DEVOTIONAL_VIEWED (from API call)
        # 6. CHECKLIST_USED (from API call)
        # 7. SCREEN_VIEW (from second middleware request - profile_view)
        
        self.assertEqual(total_events_created, 7)
        
        # Verify specific events exist
        login_event = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        ).first()
        self.assertIsNotNone(login_event)
        
        app_open_event = Event.objects.filter(
            event_type__code=EventType.APP_OPEN,
            user=self.user
        ).first()
        self.assertIsNotNone(app_open_event)
        
        session_start_event = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        self.assertIsNotNone(session_start_event)
        
        screen_view_events = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).order_by('timestamp')
        self.assertEqual(screen_view_events.count(), 2)
        self.assertEqual(screen_view_events[0].data['screen'], 'fasts_list')
        self.assertEqual(screen_view_events[1].data['screen'], 'profile_view')
        
        devotional_event = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user
        ).first()
        self.assertIsNotNone(devotional_event)
        
        checklist_event = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user
        ).first()
        self.assertIsNotNone(checklist_event)
        
        # Verify session consistency
        session_id = session_start_event.data['session_id']
        self.assertEqual(app_open_event.data['session_id'], session_id)
        self.assertEqual(screen_view_events[0].data['session_id'], session_id)
        self.assertEqual(screen_view_events[1].data['session_id'], session_id)
    
    def test_attribution_tracking_end_to_end(self):
        """Test UTM attribution from request to fast join event."""
        # Step 1: User arrives with UTM parameters
        request = self.factory.get(
            '/api/fasts/?utm_source=facebook&utm_campaign=lent2024&join_source=social_media'
        )
        request.user = self.user
        
        # Clear existing UTM data
        self.profile.utm_source = None
        self.profile.utm_campaign = None
        self.profile.join_source = None
        self.profile.save()
        
        self.middleware.process_request(request)
        
        # Verify UTM data was captured
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.utm_source, 'facebook')
        self.assertEqual(self.profile.utm_campaign, 'lent2024')
        self.assertEqual(self.profile.join_source, 'social_media')
        
        # Step 2: User joins a fast (this should include attribution data)
        initial_event_count = Event.objects.count()
        
        # Add user to fast (this triggers the signal)
        self.profile.fasts.add(self.fast)
        
        # Verify USER_JOINED_FAST event was created with attribution data
        join_event = Event.objects.filter(
            event_type__code=EventType.USER_JOINED_FAST,
            user=self.user
        ).order_by('-timestamp').first()
        
        self.assertIsNotNone(join_event)
        self.assertEqual(join_event.data['utm_source'], 'facebook')
        self.assertEqual(join_event.data['utm_campaign'], 'lent2024')
        self.assertEqual(join_event.data['join_source'], 'social_media')
        self.assertEqual(join_event.data['fast_id'], self.fast.id)
        self.assertEqual(join_event.data['fast_name'], self.fast.name)
    
    def test_session_timeout_and_continuation(self):
        """Test session timeout behavior and new session creation."""
        # Step 1: Create initial session
        request1 = self.factory.get('/api/fasts/')
        request1.user = self.user
        
        self.middleware.process_request(request1)
        
        # Get session ID
        session_start_event = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        original_session_id = session_start_event.data['session_id']
        
        initial_event_count = Event.objects.count()
        
        # Step 2: Simulate session timeout
        sess_key = f"analytics:session:{self.user.id}"
        last_seen_key = f"analytics:last_seen:{self.user.id}"
        
        # Set last seen time to 31 minutes ago (past timeout)
        past_time = timezone.now() - timedelta(minutes=31)
        cache.set(last_seen_key, past_time, timeout=3600)
        
        # Step 3: Make new request after timeout
        request2 = self.factory.get('/api/profile/')
        request2.user = self.user
        
        self.middleware.process_request(request2)
        
        # Step 4: Verify session end and new session events
        # Should create: SESSION_END, APP_OPEN, SESSION_START, SCREEN_VIEW
        new_events_count = Event.objects.count() - initial_event_count
        self.assertEqual(new_events_count, 4)
        
        # Verify session end event
        session_end_event = Event.objects.filter(
            event_type__code=EventType.SESSION_END,
            user=self.user
        ).first()
        
        self.assertIsNotNone(session_end_event)
        self.assertEqual(session_end_event.data['session_id'], original_session_id)
        self.assertIn('duration_seconds', session_end_event.data)
        
        # Verify new session
        new_session_events = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).order_by('-timestamp')
        
        self.assertEqual(new_session_events.count(), 2)
        new_session_event = new_session_events.first()
        new_session_id = new_session_event.data['session_id']
        
        self.assertNotEqual(original_session_id, new_session_id)
    
    def test_multiple_users_concurrent_sessions(self):
        """Test that multiple users can have concurrent sessions without interference."""
        # Create second user
        user2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        
        profile2 = Profile.objects.create(
            user=user2,
            church=self.church
        )
        
        initial_event_count = Event.objects.count()
        
        # User 1 starts session
        request1 = self.factory.get('/api/fasts/', HTTP_X_SCREEN='fasts_list')
        request1.user = self.user
        
        self.middleware.process_request(request1)
        
        # User 2 starts session
        request2 = self.factory.get('/api/profile/', HTTP_X_SCREEN='profile_view')
        request2.user = user2
        
        self.middleware.process_request(request2)
        
        # Both users should have separate sessions
        total_events = Event.objects.count() - initial_event_count
        self.assertEqual(total_events, 6)  # 3 events per user (APP_OPEN, SESSION_START, SCREEN_VIEW)
        
        # Verify separate session IDs
        user1_session = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        
        user2_session = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=user2
        ).first()
        
        self.assertNotEqual(user1_session.data['session_id'], user2_session.data['session_id'])
        
        # Verify screen views are correctly attributed
        user1_screen_view = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).first()
        
        user2_screen_view = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=user2
        ).first()
        
        self.assertEqual(user1_screen_view.data['screen'], 'fasts_list')
        self.assertEqual(user2_screen_view.data['screen'], 'profile_view')
    
    def test_analytics_data_consistency_across_requests(self):
        """Test that analytics data remains consistent across multiple requests."""
        # Set UTM parameters and app metadata
        request1 = self.factory.get(
            '/api/fasts/?utm_source=instagram&utm_campaign=easter2024',
            HTTP_X_APP_VERSION='2.1.0',
            HTTP_X_PLATFORM='android'
        )
        request1.user = self.user
        
        self.middleware.process_request(request1)
        
        # Make second request in same session
        request2 = self.factory.get('/api/profile/')
        request2.user = self.user
        
        self.middleware.process_request(request2)
        
        # Verify UTM data persisted
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.utm_source, 'instagram')
        self.assertEqual(self.profile.utm_campaign, 'easter2024')
        
        # Verify session consistency
        session_events = Event.objects.filter(
            event_type__code__in=[EventType.SESSION_START, EventType.SCREEN_VIEW],
            user=self.user
        ).order_by('timestamp')
        
        # All events in same session should have same session_id
        session_id = session_events.first().data['session_id']
        for event in session_events:
            if 'session_id' in event.data:
                self.assertEqual(event.data['session_id'], session_id)
        
        # App metadata should be captured in session start
        session_start = Event.objects.filter(
            event_type__code=EventType.SESSION_START,
            user=self.user
        ).first()
        
        self.assertEqual(session_start.data['app_version'], '2.1.0')
        self.assertEqual(session_start.data['platform'], 'android')
    
    def test_error_resilience_in_complete_flow(self):
        """Test that analytics errors don't break the complete user flow."""
        # Step 1: Login should work even if event creation fails
        with patch('events.models.Event.create_event', side_effect=Exception('DB Error')):
            login_url = reverse('token_obtain_pair')
            login_data = {
                'username': self.user.email,
                'password': 'testpass123'
            }
            
            login_response = self.client.post(login_url, login_data, format='json')
            self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        
        # Step 2: Middleware should work even if event creation fails
        with patch('events.models.Event.create_event', side_effect=Exception('DB Error')):
            request = self.factory.get('/api/fasts/')
            request.user = self.user
            
            # Should not raise exception
            result = self.middleware.process_request(request)
            self.assertIsNone(result)
        
        # Step 3: Engagement endpoints should work even if event creation fails
        access_token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        
        with patch('events.models.Event.create_event', side_effect=Exception('DB Error')):
            devotional_url = reverse('events:track-devotional-viewed')
            devotional_data = {'devotional_id': self.devotional.id}
            
            devotional_response = self.client.post(devotional_url, devotional_data, format='json')
            # Should return error but not crash
            self.assertEqual(devotional_response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def test_analytics_performance_with_high_activity(self):
        """Test analytics system performance with high user activity."""
        # Simulate high activity session
        session_requests = []
        
        # Create multiple requests in quick succession
        for i in range(10):
            request = self.factory.get(f'/api/endpoint{i}/', HTTP_X_SCREEN=f'screen_{i}')
            request.user = self.user
            session_requests.append(request)
        
        initial_event_count = Event.objects.count()
        
        # Process all requests
        for request in session_requests:
            self.middleware.process_request(request)
        
        # Should create: APP_OPEN, SESSION_START, and 10 SCREEN_VIEWs
        total_events = Event.objects.count() - initial_event_count
        self.assertEqual(total_events, 12)
        
        # All screen views should be in same session
        screen_views = Event.objects.filter(
            event_type__code=EventType.SCREEN_VIEW,
            user=self.user
        ).order_by('timestamp')
        
        self.assertEqual(screen_views.count(), 10)
        
        # Verify session consistency
        session_id = screen_views.first().data['session_id']
        for screen_view in screen_views:
            self.assertEqual(screen_view.data['session_id'], session_id)
        
        # Verify request counter in cache
        sess_key = f"analytics:session:{self.user.id}"
        session_data = cache.get(sess_key)
        
        self.assertIsNotNone(session_data)
        self.assertEqual(session_data['requests'], 10)
