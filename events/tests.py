"""
Tests for the events app.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from unittest.mock import patch

from .models import Event, EventType
from hub.models import Fast, Church, Profile

User = get_user_model()


class EventTypeModelTest(TestCase):
    """Test EventType model functionality."""
    
    def test_create_event_type(self):
        """Test creating an event type."""
        event_type = EventType.objects.create(
            code='test_event',
            name='Test Event',
            description='A test event type',
            category='user_action'
        )
        
        self.assertEqual(event_type.code, 'test_event')
        self.assertEqual(event_type.name, 'Test Event')
        self.assertTrue(event_type.is_active)
        self.assertTrue(event_type.track_in_analytics)
        self.assertFalse(event_type.requires_target)
    
    def test_get_or_create_default_types(self):
        """Test creating default event types."""
        # Should start with 0 event types
        self.assertEqual(EventType.objects.count(), 0)
        
        # Create default types
        created_types = EventType.get_or_create_default_types()
        
        # Should have created all default types
        self.assertEqual(len(created_types), len(EventType.CORE_EVENT_TYPES))
        self.assertEqual(EventType.objects.count(), len(EventType.CORE_EVENT_TYPES))
        
        # Verify specific event types exist
        self.assertTrue(EventType.objects.filter(code=EventType.USER_JOINED_FAST).exists())
        self.assertTrue(EventType.objects.filter(code=EventType.USER_LEFT_FAST).exists())
        self.assertTrue(EventType.objects.filter(code=EventType.FAST_PARTICIPANT_MILESTONE).exists())
        
        # Running again should not create duplicates
        created_types_again = EventType.get_or_create_default_types()
        self.assertEqual(len(created_types_again), 0)
        self.assertEqual(EventType.objects.count(), len(EventType.CORE_EVENT_TYPES))


class EventModelTest(TestCase):
    """Test Event model functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Create default event types
        EventType.get_or_create_default_types()
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create test church and fast
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
    
    def test_create_event_basic(self):
        """Test creating a basic event."""
        event = Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=self.user,
            title="User logged in",
            description="Test login event"
        )
        
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.event_type.code, EventType.USER_LOGGED_IN)
        self.assertEqual(event.title, "User logged in")
        self.assertEqual(event.description, "Test login event")
    
    def test_create_event_with_target(self):
        """Test creating an event with a target object."""
        event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast,
            data={'fast_name': self.fast.name}
        )
        
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.target, self.fast)
        self.assertEqual(event.event_type.code, EventType.USER_JOINED_FAST)
        self.assertEqual(event.data['fast_name'], self.fast.name)
    
    def test_create_event_auto_title(self):
        """Test automatic title generation."""
        event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast
        )
        
        expected_title = f"{self.user} joined {self.fast}"
        self.assertEqual(event.title, expected_title)
    
    def test_create_event_invalid_type(self):
        """Test creating event with invalid event type."""
        with self.assertRaises(ValueError):
            Event.create_event(
                event_type_code='invalid_event_type',
                user=self.user
            )
    
    def test_event_validation_required_target(self):
        """Test validation when target is required but not provided."""
        with self.assertRaises(Exception):  # ValidationError during save
            Event.create_event(
                event_type_code=EventType.USER_JOINED_FAST,
                user=self.user
                # Missing required target
            )
    
    def test_event_properties(self):
        """Test event model properties."""
        event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast
        )
        
        # Test age_in_hours property
        self.assertIsInstance(event.age_in_hours, (int, float))
        self.assertGreaterEqual(event.age_in_hours, 0)
        
        # Test target_model_name property
        self.assertEqual(event.target_model_name, 'fast')
        
        # Test formatted_data property
        self.assertIn('{', event.formatted_data)


class EventSignalsTest(TestCase):
    """Test event signals functionality."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create user profile
        self.profile = Profile.objects.create(
            user=self.user,
            church=self.church
        )
    
    def test_fast_join_signal(self):
        """Test that joining a fast creates an event."""
        # Should start with 1 event (FAST_CREATED from signal)
        initial_count = Event.objects.count()
        self.assertEqual(initial_count, 1)
        
        # Join the fast
        self.profile.fasts.add(self.fast)
        
        # Should have created a join event
        self.assertEqual(Event.objects.count(), initial_count + 1)
        
        join_event = Event.objects.filter(
            event_type__code=EventType.USER_JOINED_FAST
        ).first()
        self.assertIsNotNone(join_event)
        self.assertEqual(join_event.user, self.user)
        self.assertEqual(join_event.target, self.fast)
    
    def test_fast_leave_signal(self):
        """Test that leaving a fast creates an event."""
        # First join the fast
        self.profile.fasts.add(self.fast)
        initial_count = Event.objects.count()
        
        # Then leave the fast
        self.profile.fasts.remove(self.fast)
        
        # Should have created a leave event
        self.assertEqual(Event.objects.count(), initial_count + 1)
        
        leave_event = Event.objects.filter(
            event_type__code=EventType.USER_LEFT_FAST
        ).first()
        
        self.assertIsNotNone(leave_event)
        self.assertEqual(leave_event.user, self.user)
        self.assertEqual(leave_event.target, self.fast)
    
    def test_fast_creation_signal(self):
        """Test that creating a fast creates an event."""
        initial_count = Event.objects.count()
        
        # Create a new fast
        new_fast = Fast.objects.create(
            name='New Fast',
            church=self.church,
            year=2024
        )
        
        # Should have created a fast creation event
        self.assertEqual(Event.objects.count(), initial_count + 1)
        
        creation_event = Event.objects.filter(
            event_type__code=EventType.FAST_CREATED
        ).first()
        
        self.assertIsNotNone(creation_event)
        self.assertEqual(creation_event.target, new_fast)
        self.assertIsNone(creation_event.user)  # System event


class EventAPITest(APITestCase):
    """Test event API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create some test events
        self.event1 = Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=self.user
        )
        
        self.event2 = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast
        )
    
    def test_event_list_unauthenticated(self):
        """Test that unauthenticated users cannot access events."""
        url = reverse('events:event-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_event_list_authenticated(self):
        """Test event list for authenticated users."""
        self.client.force_authenticate(user=self.user)
        
        url = reverse('events:event-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Account for the FAST_CREATED event from signal + 2 manually created events
        self.assertEqual(len(response.data['results']), 3)
    
    def test_event_detail(self):
        """Test event detail endpoint."""
        self.client.force_authenticate(user=self.user)
        
        url = reverse('events:event-detail', kwargs={'pk': self.event1.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.event1.pk)
        self.assertEqual(response.data['event_type_code'], EventType.USER_LOGGED_IN)
    
    def test_my_events(self):
        """Test my events endpoint."""
        # Create another user and event
        other_user = User.objects.create_user(
            username='otheruser',
            email='other@example.com'
        )
        Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=other_user
        )
        
        self.client.force_authenticate(user=self.user)
        
        url = reverse('events:my-events')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only see current user's events
        self.assertEqual(len(response.data['results']), 2)
        for event in response.data['results']:
            self.assertEqual(event['user_username'], self.user.username)
    
    def test_event_stats(self):
        """Test event statistics endpoint."""
        self.client.force_authenticate(user=self.user)
        
        url = reverse('events:event-stats')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total_events', response.data)
        self.assertIn('events_last_24h', response.data)
        self.assertIn('top_event_types', response.data)
        self.assertGreaterEqual(response.data['total_events'], 2)
    
    def test_user_event_stats(self):
        """Test user event statistics endpoint."""
        self.client.force_authenticate(user=self.user)
        
        url = reverse('events:user-event-stats')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user_id'], self.user.id)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertGreaterEqual(response.data['total_events'], 2)
    
    def test_fast_event_stats(self):
        """Test fast event statistics endpoint."""
        self.client.force_authenticate(user=self.user)
        
        url = reverse('events:fast-event-stats', kwargs={'fast_id': self.fast.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['fast_id'], self.fast.pk)
        self.assertEqual(response.data['fast_name'], self.fast.name)
    
    def test_event_filtering(self):
        """Test event filtering options."""
        self.client.force_authenticate(user=self.user)
        
        # Filter by event type
        url = reverse('events:event-list')
        response = self.client.get(url, {'event_type': EventType.USER_LOGGED_IN})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(
            response.data['results'][0]['event_type_code'], 
            EventType.USER_LOGGED_IN
        )
        
        # Filter by user
        response = self.client.get(url, {'user': self.user.id})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)


class EventUtilsTest(TestCase):
    """Test event utility functions."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
    
    def test_track_fast_participant_milestone(self):
        """Test milestone tracking function."""
        from .signals import track_fast_participant_milestone
        
        initial_count = Event.objects.count()
        
        # Test milestone hit
        result = track_fast_participant_milestone(self.fast, 100)
        
        self.assertTrue(result)
        self.assertEqual(Event.objects.count(), initial_count + 1)
        
        milestone_event = Event.objects.filter(
            event_type__code=EventType.FAST_PARTICIPANT_MILESTONE
        ).first()
        
        self.assertIsNotNone(milestone_event)
        self.assertEqual(milestone_event.target, self.fast)
        self.assertEqual(milestone_event.data['milestone'], 100)
        
        # Test non-milestone number
        result = track_fast_participant_milestone(self.fast, 99)
        self.assertFalse(result)
        self.assertEqual(Event.objects.count(), initial_count + 1)  # No new event
    
    def test_track_devotional_available(self):
        """Test devotional availability tracking."""
        from .signals import track_devotional_available
        
        initial_count = Event.objects.count()
        
        devotional_info = {
            'devotional_id': 123,
            'title': 'Test Devotional',
            'date': '2024-01-01'
        }
        
        track_devotional_available(self.fast, devotional_info)
        
        self.assertEqual(Event.objects.count(), initial_count + 1)
        
        devotional_event = Event.objects.filter(
            event_type__code=EventType.DEVOTIONAL_AVAILABLE
        ).first()
        
        self.assertIsNotNone(devotional_event)
        self.assertEqual(devotional_event.target, self.fast)
        self.assertEqual(devotional_event.data['devotional_id'], 123)
        self.assertEqual(devotional_event.data['title'], 'Test Devotional')
    
    def test_track_fast_beginning_ending(self):
        """Test fast beginning and ending tracking."""
        from .signals import track_fast_beginning, track_fast_ending
        
        initial_count = Event.objects.count()
        
        # Test fast beginning
        track_fast_beginning(self.fast)
        self.assertEqual(Event.objects.count(), initial_count + 1)
        
        beginning_event = Event.objects.filter(
            event_type__code=EventType.FAST_BEGINNING
        ).first()
        
        self.assertIsNotNone(beginning_event)
        self.assertEqual(beginning_event.target, self.fast)
        
        # Test fast ending
        track_fast_ending(self.fast)
        self.assertEqual(Event.objects.count(), initial_count + 2)
        
        ending_event = Event.objects.filter(
            event_type__code=EventType.FAST_ENDING
        ).first()
        
        self.assertIsNotNone(ending_event)
        self.assertEqual(ending_event.target, self.fast)
