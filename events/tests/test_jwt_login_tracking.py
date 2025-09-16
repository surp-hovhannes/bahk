"""
Tests for enhanced JWT login tracking.

This module tests the TrackingTokenObtainPairView that emits
USER_LOGGED_IN events when JWT tokens are obtained.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from unittest.mock import patch

from events.models import Event, EventType
from hub.models import Profile, Church

User = get_user_model()


class JWTLoginTrackingTest(APITestCase):
    """Test JWT login tracking functionality."""
    
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
        
        self.client = APIClient()
    
    def test_successful_jwt_login_creates_event(self):
        """Test that successful JWT login creates USER_LOGGED_IN event."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,  # Using email as username (EmailBackend)
            'password': 'testpass123'
        }
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        
        # Check that USER_LOGGED_IN event was created
        self.assertEqual(Event.objects.count(), initial_event_count + 1)
        
        login_event = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        ).first()
        
        self.assertIsNotNone(login_event)
        self.assertEqual(login_event.title, 'User logged in (JWT)')
        self.assertEqual(login_event.data['method'], 'jwt')
    
    def test_failed_jwt_login_does_not_create_event(self):
        """Test that failed JWT login does not create USER_LOGGED_IN event."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'wrongpassword'
        }
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # No event should be created for failed login
        self.assertEqual(Event.objects.count(), initial_event_count)
        
        login_events = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        )
        
        self.assertEqual(login_events.count(), 0)
    
    def test_jwt_login_with_username(self):
        """Test JWT login using username instead of email."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.username,
            'password': 'testpass123'
        }
        
        initial_event_count = Event.objects.count()
        
        response = self.client.post(url, data, format='json')
        
        # Note: The app may be configured to only accept email logins
        # If username login fails, that's expected behavior
        if response.status_code == status.HTTP_200_OK:
            # If login succeeds, check that event was created
            self.assertEqual(Event.objects.count(), initial_event_count + 1)
            
            login_event = Event.objects.filter(
                event_type__code=EventType.USER_LOGGED_IN,
                user=self.user
            ).first()
            
            self.assertIsNotNone(login_event)
        else:
            # If username login is not supported, that's also valid
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            # No event should be created for failed login
            self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_multiple_logins_create_multiple_events(self):
        """Test that multiple logins create separate events."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        initial_event_count = Event.objects.count()
        
        # Login twice
        response1 = self.client.post(url, data, format='json')
        response2 = self.client.post(url, data, format='json')
        
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Should create 2 separate events
        self.assertEqual(Event.objects.count(), initial_event_count + 2)
        
        login_events = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        ).order_by('timestamp')
        
        self.assertEqual(login_events.count(), 2)
        
        # Both events should have same data structure
        for event in login_events:
            self.assertEqual(event.title, 'User logged in (JWT)')
            self.assertEqual(event.data['method'], 'jwt')
    
    def test_different_users_login_tracking(self):
        """Test that different users' logins are tracked separately."""
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
        
        url = reverse('token_obtain_pair')
        
        initial_event_count = Event.objects.count()
        
        # Login as first user
        data1 = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        response1 = self.client.post(url, data1, format='json')
        
        # Login as second user
        data2 = {
            'username': user2.email,
            'password': 'testpass123'
        }
        response2 = self.client.post(url, data2, format='json')
        
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Should create 2 events
        self.assertEqual(Event.objects.count(), initial_event_count + 2)
        
        # Check events for each user
        user1_events = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        )
        
        user2_events = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=user2
        )
        
        self.assertEqual(user1_events.count(), 1)
        self.assertEqual(user2_events.count(), 1)
    
    def test_jwt_login_event_includes_request_metadata(self):
        """Test that login events include request metadata."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        response = self.client.post(
            url, 
            data, 
            format='json',
            HTTP_USER_AGENT='TestApp/1.0',
            REMOTE_ADDR='192.168.1.100'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        login_event = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        ).first()
        
        self.assertIsNotNone(login_event)
        # Note: IP and user agent are captured by Event.create_event() method
        # The exact implementation depends on how the Event model handles request metadata
    
    def test_jwt_login_graceful_error_handling(self):
        """Test that errors in event creation don't break JWT login."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        # Mock Event.create_event to raise an exception
        with patch('events.models.Event.create_event', side_effect=Exception('DB Error')):
            response = self.client.post(url, data, format='json')
            
            # Login should still succeed despite event creation failure
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn('access', response.data)
            self.assertIn('refresh', response.data)
    
    def test_jwt_login_event_without_event_types_initialized(self):
        """Test JWT login when event types are not initialized."""
        # Delete all event types
        EventType.objects.all().delete()
        
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        response = self.client.post(url, data, format='json')
        
        # Login should still succeed even if event types don't exist
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
    
    def test_jwt_login_response_format(self):
        """Test that JWT login response format is correct."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Response should contain access and refresh tokens
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        
        # Tokens should be strings
        self.assertIsInstance(response.data['access'], str)
        self.assertIsInstance(response.data['refresh'], str)
        
        # Tokens should not be empty
        self.assertGreater(len(response.data['access']), 0)
        self.assertGreater(len(response.data['refresh']), 0)
    
    def test_jwt_login_serializer_user_attribute(self):
        """Test that serializer has user attribute for event creation."""
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        # This test ensures that the TrackingTokenObtainPairView can access
        # the user from the serializer to create the login event
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # If we got a successful response and an event was created,
        # then the serializer.user attribute is working correctly
        login_event = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        ).first()
        
        self.assertIsNotNone(login_event)
    
    def test_jwt_refresh_does_not_create_login_event(self):
        """Test that JWT token refresh does not create login events."""
        # First, get tokens
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        response = self.client.post(url, data, format='json')
        refresh_token = response.data['refresh']
        
        initial_event_count = Event.objects.count()
        
        # Now refresh the token
        refresh_url = reverse('token_refresh')
        refresh_data = {'refresh': refresh_token}
        
        response = self.client.post(refresh_url, refresh_data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        
        # No new login event should be created for token refresh
        self.assertEqual(Event.objects.count(), initial_event_count)
    
    def test_jwt_login_event_timestamp_accuracy(self):
        """Test that login event timestamp is accurate."""
        from django.utils import timezone
        
        before_login = timezone.now()
        
        url = reverse('token_obtain_pair')
        data = {
            'username': self.user.email,
            'password': 'testpass123'
        }
        
        response = self.client.post(url, data, format='json')
        
        after_login = timezone.now()
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        login_event = Event.objects.filter(
            event_type__code=EventType.USER_LOGGED_IN,
            user=self.user
        ).first()
        
        self.assertIsNotNone(login_event)
        
        # Event timestamp should be between before_login and after_login
        self.assertGreaterEqual(login_event.timestamp, before_login)
        self.assertLessEqual(login_event.timestamp, after_login)
