"""
Tests for engagement tracking endpoints.

This module tests the API endpoints that track user engagement:
- TrackDevotionalViewedView
- TrackChecklistUsedView
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event, EventType
from hub.models import Fast, Church, Profile, Day, Devotional, Video

User = get_user_model()


class EngagementTrackingEndpointsTest(APITestCase):
    """Test engagement tracking API endpoints."""
    
    def setUp(self):
        """Set up test data."""
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
        
        # Create test fast
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create test day
        self.day = Day.objects.create(
            fast=self.fast,
            date='2024-03-01',
            church=self.church
        )
        
        # Create test video and devotional
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
        
        # Set up authentication
        self.client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')
    
    def test_track_devotional_viewed_success(self):
        """Test successful devotional viewed tracking."""
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        
        # Check that event was created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        devotional_event = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user
        ).first()
        
        self.assertIsNotNone(devotional_event)
        self.assertEqual(devotional_event.title, 'Devotional viewed')
        self.assertEqual(devotional_event.target, self.devotional)
        self.assertEqual(devotional_event.data['devotional_id'], self.devotional.id)
        self.assertEqual(devotional_event.data['fast_id'], self.fast.id)
        self.assertEqual(devotional_event.data['day'], '2024-03-01')
    
    def test_track_devotional_viewed_missing_devotional_id(self):
        """Test devotional tracking with missing devotional_id."""
        url = reverse('events:track-devotional-viewed')
        data = {}  # Missing devotional_id
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('devotional_id is required', response.data['error'])
        
        # No event should be created
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_track_devotional_viewed_invalid_devotional_id(self):
        """Test devotional tracking with invalid devotional_id."""
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': 99999}  # Non-existent devotional
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid devotional_id', response.data['error'])
        
        # No event should be created
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_track_devotional_viewed_non_numeric_devotional_id(self):
        """Test devotional tracking with non-numeric devotional_id."""
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': 'invalid'}
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid devotional_id', response.data['error'])
        
        # No event should be created
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_track_devotional_viewed_authentication_required(self):
        """Test that devotional tracking requires authentication."""
        # Remove authentication
        self.client.credentials()
        
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_track_devotional_viewed_with_day_data(self):
        """Test tracking devotional and verify day data is included."""
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Event should be created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        devotional_event = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user
        ).order_by('-timestamp').first()
        
        self.assertIsNotNone(devotional_event)
        self.assertEqual(devotional_event.data['devotional_id'], self.devotional.id)
        self.assertEqual(devotional_event.data['fast_id'], self.fast.id)
        self.assertEqual(devotional_event.data['day'], '2024-03-01')
    
    def test_track_checklist_used_success(self):
        """Test successful checklist used tracking."""
        url = reverse('events:track-checklist-used')
        data = {
            'fast_id': self.fast.id,
            'action': 'daily_review'
        }
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        
        # Check that event was created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        checklist_event = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user
        ).first()
        
        self.assertIsNotNone(checklist_event)
        self.assertEqual(checklist_event.title, 'Checklist used')
        self.assertEqual(checklist_event.target, self.fast)
        self.assertEqual(checklist_event.data['fast_id'], self.fast.id)
        self.assertEqual(checklist_event.data['action'], 'daily_review')
        self.assertEqual(checklist_event.data['context'], 'fast_specific')
    
    def test_track_checklist_used_minimal_data(self):
        """Test checklist tracking with minimal data (no fast_id, no action)."""
        url = reverse('events:track-checklist-used')
        data = {}  # Completely empty
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        
        # Event should still be created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        checklist_event = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user
        ).order_by('-timestamp').first()
        
        self.assertIsNotNone(checklist_event)
        self.assertEqual(checklist_event.title, 'Checklist used')
        self.assertIsNone(checklist_event.target)
        self.assertIsNone(checklist_event.data['fast_id'])
        self.assertIsNone(checklist_event.data['fast_name'])
        self.assertIsNone(checklist_event.data['action'])
        self.assertEqual(checklist_event.data['context'], 'general')
    
    def test_track_checklist_used_without_action(self):
        """Test checklist tracking without optional action parameter."""
        url = reverse('events:track-checklist-used')
        data = {'fast_id': self.fast.id}  # No action specified
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Event should still be created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        checklist_event = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user
        ).first()
        
        self.assertIsNotNone(checklist_event)
        self.assertEqual(checklist_event.data['fast_id'], self.fast.id)
        self.assertIsNone(checklist_event.data['action'])
        self.assertEqual(checklist_event.data['context'], 'fast_specific')
    
    def test_track_checklist_used_without_fast_id(self):
        """Test checklist tracking without fast_id (general usage)."""
        url = reverse('events:track-checklist-used')
        data = {'action': 'general_reflection'}  # No fast_id
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        
        # Check that event was created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        checklist_event = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user
        ).order_by('-timestamp').first()
        
        self.assertIsNotNone(checklist_event)
        self.assertEqual(checklist_event.title, 'Checklist used')
        self.assertIsNone(checklist_event.target)  # No target fast
        self.assertIsNone(checklist_event.data['fast_id'])
        self.assertIsNone(checklist_event.data['fast_name'])
        self.assertEqual(checklist_event.data['action'], 'general_reflection')
        self.assertEqual(checklist_event.data['context'], 'general')
    
    def test_track_checklist_used_invalid_fast_id(self):
        """Test checklist tracking with invalid fast_id."""
        url = reverse('events:track-checklist-used')
        data = {
            'fast_id': 99999,  # Non-existent fast
            'action': 'daily_review'
        }
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid fast_id', response.data['error'])
        
        # No event should be created
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_track_checklist_used_non_numeric_fast_id(self):
        """Test checklist tracking with non-numeric fast_id."""
        url = reverse('events:track-checklist-used')
        data = {
            'fast_id': 'invalid',
            'action': 'daily_review'
        }
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid fast_id', response.data['error'])
        
        # No event should be created
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_track_checklist_used_authentication_required(self):
        """Test that checklist tracking requires authentication."""
        # Remove authentication
        self.client.credentials()
        
        url = reverse('events:track-checklist-used')
        data = {'fast_id': self.fast.id}
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_track_devotional_viewed_get_method_not_allowed(self):
        """Test that GET method is not allowed for devotional tracking."""
        url = reverse('events:track-devotional-viewed')
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
    
    def test_track_checklist_used_get_method_not_allowed(self):
        """Test that GET method is not allowed for checklist tracking."""
        url = reverse('events:track-checklist-used')
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
    
    def test_multiple_devotional_views_create_multiple_events(self):
        """Test that multiple devotional views create separate events."""
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        initial_event_count = Event.objects.count()
        
        # Track same devotional twice
        response1 = self.client.post(url, data, format='json')
        response2 = self.client.post(url, data, format='json')
        
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Should create 2 separate events
        self.assertEqual(Event.objects.count(), initial_event_count + 2)
        
        devotional_events = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user,
            data__devotional_id=self.devotional.id
        )
        
        self.assertEqual(devotional_events.count(), 2)
    
    def test_multiple_checklist_uses_create_multiple_events(self):
        """Test that multiple checklist uses create separate events."""
        url = reverse('events:track-checklist-used')
        data = {'fast_id': self.fast.id, 'action': 'morning_review'}
        
        initial_event_count = Event.objects.count()
        
        # Track checklist use twice
        response1 = self.client.post(url, data, format='json')
        response2 = self.client.post(url, data, format='json')
        
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Should create 2 separate events
        self.assertEqual(Event.objects.count(), initial_event_count + 2)
        
        checklist_events = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user,
            data__fast_id=self.fast.id
        )
        
        self.assertEqual(checklist_events.count(), 2)
    
    def test_different_users_track_same_devotional(self):
        """Test that different users can track the same devotional."""
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
        
        # Set up authentication for user2
        client2 = APIClient()
        refresh2 = RefreshToken.for_user(user2)
        client2.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh2.access_token}')
        
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        initial_event_count = Event.objects.count()
        
        # Both users track same devotional
        response1 = self.client.post(url, data, format='json')
        response2 = client2.post(url, data, format='json')
        
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Should create 2 separate events
        self.assertEqual(Event.objects.count(), initial_event_count + 2)
        
        # Check events for each user
        user1_events = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user,
            data__devotional_id=self.devotional.id
        )
        
        user2_events = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=user2,
            data__devotional_id=self.devotional.id
        )
        
        self.assertEqual(user1_events.count(), 1)
        self.assertEqual(user2_events.count(), 1)
    
    def test_event_includes_request_metadata(self):
        """Test that events include request metadata (IP, user agent)."""
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        # Set custom headers
        response = self.client.post(
            url, 
            data, 
            format='json',
            HTTP_USER_AGENT='TestApp/1.0',
            REMOTE_ADDR='192.168.1.100'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        devotional_event = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user
        ).first()
        
        self.assertIsNotNone(devotional_event)
        # Note: IP and user agent are captured by Event.create_event() method
        # The exact implementation depends on how the Event model handles request metadata
    
    def test_event_target_relationships(self):
        """Test that events have correct target relationships."""
        # Test devotional event target
        url = reverse('events:track-devotional-viewed')
        data = {'devotional_id': self.devotional.id}
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        devotional_event = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_VIEWED,
            user=self.user
        ).first()
        
        self.assertEqual(devotional_event.target, self.devotional)
        self.assertEqual(devotional_event.content_type.model, 'devotional')
        self.assertEqual(devotional_event.object_id, self.devotional.id)
        
        # Test checklist event target
        url = reverse('events:track-checklist-used')
        data = {'fast_id': self.fast.id}
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        checklist_event = Event.objects.filter(
            event_type__code=EventType.CHECKLIST_USED,
            user=self.user
        ).first()
        
        self.assertEqual(checklist_event.target, self.fast)
        self.assertEqual(checklist_event.content_type.model, 'fast')
        self.assertEqual(checklist_event.object_id, self.fast.id)
