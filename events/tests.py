"""
Tests for the events app.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from unittest.mock import patch

from .models import Event, EventType, UserActivityFeed
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
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
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
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create a test event
        self.event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast,
            title="User joined fast",
            description="Test join event"
        )
    
    def test_fast_join_signal(self):
        """Test that joining a fast creates an event."""
        # Should start with 2 events (FAST_CREATED from signal + manual test event)
        initial_count = Event.objects.count()
        self.assertEqual(initial_count, 2)
        
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
        
        # Should also have created a feed item
        feed_item = UserActivityFeed.objects.filter(
            user=self.user,
            activity_type='fast_join'
        ).first()
        self.assertIsNotNone(feed_item)
        self.assertEqual(feed_item.event, join_event)
    
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
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
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
        # Create profile for other user
        other_profile = Profile.objects.create(user=other_user)
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
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
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


class UserActivityFeedModelTest(TestCase):
    """Test UserActivityFeed model functionality."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create a test event
        self.event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast,
            title="User joined fast",
            description="Test join event"
        )
    
    def test_create_activity_feed_item(self):
        """Test creating a basic activity feed item."""
        feed_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_join',
            title='Test Activity',
            description='Test description',
            data={'fast_id': self.fast.id}
        )
        
        self.assertEqual(feed_item.user, self.user)
        self.assertEqual(feed_item.activity_type, 'fast_join')
        self.assertEqual(feed_item.title, 'Test Activity')
        self.assertFalse(feed_item.is_read)
        self.assertIsNone(feed_item.read_at)
    
    def test_mark_as_read(self):
        """Test marking a feed item as read."""
        feed_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_join',
            title='Test Activity',
            description='Test description'
        )
        
        self.assertFalse(feed_item.is_read)
        self.assertIsNone(feed_item.read_at)
        
        feed_item.mark_as_read()
        feed_item.refresh_from_db()
        
        self.assertTrue(feed_item.is_read)
        self.assertIsNotNone(feed_item.read_at)
    
    def test_create_from_event(self):
        """Test creating feed item from event."""
        feed_item = UserActivityFeed.create_from_event(self.event, self.user)
        
        self.assertIsNotNone(feed_item)
        self.assertEqual(feed_item.user, self.user)
        self.assertEqual(feed_item.activity_type, 'fast_join')
        self.assertEqual(feed_item.event, self.event)
        self.assertEqual(feed_item.target, self.fast)
        self.assertEqual(feed_item.title, self.event.title)
        self.assertEqual(feed_item.description, self.event.description)
    
    def test_create_from_event_no_user(self):
        """Test creating feed item from event with no user."""
        # Create system event (no user)
        system_event = Event.create_event(
            event_type_code=EventType.FAST_BEGINNING,
            user=None,
            target=self.fast,
            title="Fast began",
            description="System event"
        )
        
        feed_item = UserActivityFeed.create_from_event(system_event)
        self.assertIsNone(feed_item)
    
    def test_create_from_event_uninteresting_type(self):
        """Test creating feed item from uninteresting event type."""
        # Create event with uninteresting type
        uninteresting_event = Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=self.user,
            title="User logged in",
            description="Login event"
        )
        
        feed_item = UserActivityFeed.create_from_event(uninteresting_event, self.user)
        self.assertIsNone(feed_item)
    
    def test_create_fast_reminder(self):
        """Test creating fast reminder feed item."""
        feed_item = UserActivityFeed.create_fast_reminder(self.user, self.fast)
        
        self.assertEqual(feed_item.user, self.user)
        self.assertEqual(feed_item.activity_type, 'fast_reminder')
        self.assertEqual(feed_item.target, self.fast)
        self.assertIn('Fast Reminder', feed_item.title)
        self.assertIn(self.fast.name, feed_item.description)
        self.assertEqual(feed_item.data['fast_id'], self.fast.id)
        self.assertEqual(feed_item.data['fast_name'], self.fast.name)
    
    def test_create_devotional_reminder(self):
        """Test creating devotional reminder feed item."""
        from hub.models import Devotional, Day
        from learning_resources.models import Video
        
        # Create test video first
        video = Video.objects.create(
            title='Test Devotional Video',
            description='Test devotional description',
            category='devotional'
        )
        
        # Create test devotional
        day = Day.objects.create(date='2024-01-15', fast=self.fast)
        devotional = Devotional.objects.create(
            day=day,
            video=video
        )
        
        feed_item = UserActivityFeed.create_devotional_reminder(self.user, devotional, self.fast)
        
        self.assertEqual(feed_item.user, self.user)
        self.assertEqual(feed_item.activity_type, 'devotional_reminder')
        self.assertEqual(feed_item.target, devotional)
        self.assertIn('New Devotional', feed_item.title)
        self.assertIn(self.fast.name, feed_item.description)
        self.assertEqual(feed_item.data['devotional_id'], devotional.id)
        self.assertEqual(feed_item.data['fast_id'], self.fast.id)
    
    def test_retention_policy(self):
        """Test retention policy configuration."""
        policy = UserActivityFeed.get_retention_policy()
        
        self.assertIn('fast_reminder', policy)
        self.assertIn('devotional_reminder', policy)
        self.assertIn('fast_join', policy)
        self.assertIn('milestone', policy)
        
        self.assertEqual(policy['fast_reminder'], 30)
        self.assertEqual(policy['milestone'], 365)
    
    def test_cleanup_old_items(self):
        """Test cleaning up old items."""
        from django.utils import timezone
        from datetime import timedelta
        
        # Create old read items (much older than retention policy - 100 days for fast_reminder)
        old_date = timezone.now() - timedelta(days=100)
        old_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_reminder',
            title='Old Item',
            description='Old description',
            is_read=True
        )
        # Update the created_at timestamp to be old
        old_item.created_at = old_date
        old_item.save()
        
        # Create recent unread item
        recent_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_reminder',
            title='Recent Item',
            description='Recent description',
            is_read=False
        )
        
        # Run cleanup (dry run)
        deleted_count = UserActivityFeed.cleanup_old_items(dry_run=True)
        self.assertEqual(deleted_count, 1)  # Only old read item
        
        # Verify items still exist (dry run)
        self.assertTrue(UserActivityFeed.objects.filter(id=old_item.id).exists())
        self.assertTrue(UserActivityFeed.objects.filter(id=recent_item.id).exists())
        
        # Run actual cleanup
        deleted_count = UserActivityFeed.cleanup_old_items(dry_run=False)
        self.assertEqual(deleted_count, 1)
        
        # Verify old item is deleted, recent item remains
        self.assertFalse(UserActivityFeed.objects.filter(id=old_item.id).exists())
        self.assertTrue(UserActivityFeed.objects.filter(id=recent_item.id).exists())
    
    def test_get_user_feed_stats(self):
        """Test getting user feed statistics."""
        # Clear any existing feed items for this user
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Create multiple feed items
        UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_join',
            title='Join 1',
            is_read=True
        )
        UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_join',
            title='Join 2',
            is_read=False
        )
        UserActivityFeed.objects.create(
            user=self.user,
            activity_type='milestone',
            title='Milestone',
            is_read=False
        )
        
        stats = UserActivityFeed.get_user_feed_stats(self.user)
        
        self.assertEqual(stats['total_items'], 3)
        self.assertEqual(stats['unread_count'], 2)
        self.assertEqual(stats['read_count'], 1)
        self.assertEqual(stats['by_type']['fast_join'], 2)
        self.assertEqual(stats['by_type']['milestone'], 1)
        # Skip by_month test as it uses database-specific functions


class UserActivityFeedAPITest(APITestCase):
    """Test UserActivityFeed API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create test feed items
        self.feed_item1 = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_join',
            title='Join Activity',
            description='User joined fast',
            is_read=False
        )
        
        self.feed_item2 = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='milestone',
            title='Milestone Activity',
            description='User reached milestone',
            is_read=True
        )
        
        self.client.force_authenticate(user=self.user)
    
    def test_activity_feed_list_unauthenticated(self):
        """Test activity feed list without authentication."""
        self.client.force_authenticate(user=None)
        url = reverse('events:activity-feed')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_activity_feed_list_authenticated(self):
        """Test activity feed list with authentication."""
        url = reverse('events:activity-feed')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
        
        # Check that both items are present (order may vary)
        activity_types = [item['activity_type'] for item in response.data['results']]
        self.assertIn('fast_join', activity_types)
        self.assertIn('milestone', activity_types)
        
        # Check that one item is unread
        unread_items = [item for item in response.data['results'] if not item['is_read']]
        self.assertEqual(len(unread_items), 1)
    
    def test_activity_feed_filter_by_type(self):
        """Test filtering activity feed by type."""
        url = reverse('events:activity-feed')
        response = self.client.get(url, {'activity_type': 'fast_join'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['activity_type'], 'fast_join')
    
    def test_activity_feed_filter_by_read_status(self):
        """Test filtering activity feed by read status."""
        url = reverse('events:activity-feed')
        response = self.client.get(url, {'is_read': 'false'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertFalse(response.data['results'][0]['is_read'])
    
    def test_activity_feed_summary(self):
        """Test activity feed summary endpoint."""
        url = reverse('events:activity-feed-summary')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_items'], 2)
        self.assertEqual(response.data['unread_count'], 1)
        self.assertEqual(response.data['read_count'], 1)
        self.assertEqual(response.data['activity_types']['fast_join'], 1)
        self.assertEqual(response.data['activity_types']['milestone'], 1)
        self.assertEqual(len(response.data['recent_activity']), 2)
    
    def test_mark_activity_read_specific(self):
        """Test marking specific activities as read."""
        url = reverse('events:mark-activity-read')
        response = self.client.post(url, {
            'activity_ids': [self.feed_item1.id]
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['updated_count'], 1)
        
        # Verify item is marked as read
        self.feed_item1.refresh_from_db()
        self.assertTrue(self.feed_item1.is_read)
        self.assertIsNotNone(self.feed_item1.read_at)
    
    def test_mark_activity_read_all(self):
        """Test marking all activities as read."""
        url = reverse('events:mark-activity-read')
        response = self.client.post(url, {
            'mark_all': True
        })
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['updated_count'], 1)  # Only unread item
        
        # Verify all items are marked as read
        self.feed_item1.refresh_from_db()
        self.assertTrue(self.feed_item1.is_read)
    
    def test_mark_activity_read_invalid_request(self):
        """Test marking activities as read with invalid request."""
        url = reverse('events:mark-activity-read')
        response = self.client.post(url, {})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_mark_activity_read_invalid_ids(self):
        """Test marking activities as read with invalid IDs."""
        url = reverse('events:mark-activity-read')
        response = self.client.post(url, {
            'activity_ids': 'not_a_list'
        })
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UserActivityFeedSignalsTest(TestCase):
    """Test UserActivityFeed signal functionality."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
    
    def test_signal_creates_feed_item_sync(self):
        """Test that signal creates feed item synchronously."""
        from django.conf import settings
        
        # Ensure sync mode
        with self.settings(USE_ASYNC_ACTIVITY_FEED=False):
            # Create event (should trigger signal)
            event = Event.create_event(
                event_type_code=EventType.USER_JOINED_FAST,
                user=self.user,
                target=self.fast,
                title="User joined fast",
                description="Test join event"
            )
            
            # Check that feed item was created
            feed_item = UserActivityFeed.objects.filter(
                user=self.user,
                event=event
            ).first()
            
            self.assertIsNotNone(feed_item)
            self.assertEqual(feed_item.activity_type, 'fast_join')
    
    @patch('events.tasks.create_activity_feed_item_task')
    def test_signal_creates_feed_item_async(self, mock_task):
        """Test that signal creates feed item asynchronously."""
        from django.conf import settings
        
        # Ensure async mode
        with self.settings(USE_ASYNC_ACTIVITY_FEED=True):
            # Create event (should trigger signal)
            event = Event.create_event(
                event_type_code=EventType.USER_JOINED_FAST,
                user=self.user,
                target=self.fast,
                title="User joined fast",
                description="Test join event"
            )
            
            # Check that Celery task was called
            mock_task.delay.assert_called_once_with(event.id, self.user.id)
            
            # Feed item should not exist yet (async)
            feed_item = UserActivityFeed.objects.filter(
                user=self.user,
                event=event
            ).first()
            self.assertIsNone(feed_item)
    
    def test_signal_no_user(self):
        """Test signal with event that has no user."""
        # Create system event (no user)
        event = Event.create_event(
            event_type_code=EventType.FAST_BEGINNING,
            user=None,
            target=self.fast,
            title="Fast began",
            description="System event"
        )
        
        # Should not create feed item
        feed_item = UserActivityFeed.objects.filter(event=event).first()
        self.assertIsNone(feed_item)


class UserActivityFeedTasksTest(TestCase):
    """Test UserActivityFeed Celery tasks."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        self.event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast,
            title="User joined fast",
            description="Test join event"
        )
    
    def test_create_activity_feed_item_task(self):
        """Test creating activity feed item via Celery task."""
        from .tasks import create_activity_feed_item_task
        
        # Run task synchronously
        result = create_activity_feed_item_task(self.event.id, self.user.id)
        
        # Check that feed item was created
        feed_item = UserActivityFeed.objects.filter(
            user=self.user,
            event=self.event
        ).first()
        
        self.assertIsNotNone(feed_item)
        self.assertEqual(feed_item.activity_type, 'fast_join')
    
    def test_create_activity_feed_item_task_no_event(self):
        """Test task with non-existent event."""
        from .tasks import create_activity_feed_item_task
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Run task with invalid event ID
        result = create_activity_feed_item_task(99999, self.user.id)
        
        # Should not create feed item
        feed_item = UserActivityFeed.objects.filter(user=self.user).first()
        self.assertIsNone(feed_item)
    
    def test_create_fast_reminder_feed_items_task(self):
        """Test creating fast reminder feed items via Celery task."""
        from .tasks import create_fast_reminder_feed_items_task
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Add user to fast
        self.user.profile.fasts.add(self.fast)
        
        # Run task synchronously
        result = create_fast_reminder_feed_items_task(self.fast.id, 'fast_reminder')
        
        # Check that feed item was created
        feed_item = UserActivityFeed.objects.filter(
            user=self.user,
            activity_type='fast_reminder'
        ).first()
        
        self.assertIsNotNone(feed_item)
        self.assertEqual(feed_item.target, self.fast)
        self.assertEqual(result, 1)  # One item created
    
    def test_batch_create_activity_feed_items_task(self):
        """Test batch creating activity feed items via Celery task."""
        from .tasks import batch_create_activity_feed_items_task
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Create another event
        event2 = Event.create_event(
            event_type_code=EventType.USER_LEFT_FAST,
            user=self.user,
            target=self.fast,
            title="User left fast",
            description="Test leave event"
        )
        
        # Run task synchronously
        result = batch_create_activity_feed_items_task([self.event.id, event2.id])
        
        # Check that feed items were created (signal may have created one, so we expect 2-3)
        feed_items = UserActivityFeed.objects.filter(user=self.user)
        self.assertGreaterEqual(feed_items.count(), 2)
        self.assertLessEqual(feed_items.count(), 3)
        self.assertEqual(result, 2)  # Two items created by task
    
    def test_cleanup_old_activity_feed_items_task(self):
        """Test cleaning up old activity feed items via Celery task."""
        from .tasks import cleanup_old_activity_feed_items_task
        from django.utils import timezone
        from datetime import timedelta
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Create old read item
        old_date = timezone.now() - timedelta(days=100)
        old_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_reminder',
            title='Old Item',
            description='Old description',
            is_read=True
        )
        # Update the created_at timestamp to be old
        old_item.created_at = old_date
        old_item.save()
        
        # Run task synchronously
        result = cleanup_old_activity_feed_items_task()
        
        # Check that old item was deleted
        self.assertFalse(UserActivityFeed.objects.filter(id=old_item.id).exists())
        self.assertEqual(result, 1)  # One item deleted
    
    def test_populate_user_activity_feed_task(self):
        """Test populating user activity feed via Celery task."""
        from .tasks import populate_user_activity_feed_task
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Run task synchronously
        result = populate_user_activity_feed_task(self.user.id, days_back=30)
        
        # Check that feed item was created from existing event
        feed_item = UserActivityFeed.objects.filter(
            user=self.user,
            event=self.event
        ).first()
        
        self.assertIsNotNone(feed_item)
        self.assertEqual(result, 1)  # One item created


class UserActivityFeedManagementCommandsTest(TestCase):
    """Test UserActivityFeed management commands."""
    
    def setUp(self):
        """Set up test data."""
        EventType.get_or_create_default_types()
        
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )
        
        # Create profile for user
        self.profile = Profile.objects.create(user=self.user)
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        self.event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.user,
            target=self.fast,
            title="User joined fast",
            description="Test join event"
        )
    
    def test_populate_activity_feeds_command(self):
        """Test populate_activity_feeds management command."""
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        out = StringIO()
        
        # Run command
        call_command('populate_activity_feeds', stdout=out)
        
        # Check output
        output = out.getvalue()
        self.assertIn('Created: 1', output)
        self.assertIn('Successfully created 1 activity feed items', output)
        
        # Check that feed item was created
        feed_item = UserActivityFeed.objects.filter(
            user=self.user,
            event=self.event
        ).first()
        self.assertIsNotNone(feed_item)
    
    def test_populate_activity_feeds_command_dry_run(self):
        """Test populate_activity_feeds command with dry run."""
        from django.core.management import call_command
        from io import StringIO
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        out = StringIO()
        
        # Run command with dry run
        call_command('populate_activity_feeds', dry_run=True, stdout=out)
        
        # Check output
        output = out.getvalue()
        self.assertIn('DRY RUN: Would create 1 activity feed items', output)
        
        # Check that no feed item was actually created
        feed_item = UserActivityFeed.objects.filter(
            user=self.user,
            event=self.event
        ).first()
        self.assertIsNone(feed_item)
    
    def test_populate_activity_feeds_command_specific_user(self):
        """Test populate_activity_feeds command for specific user."""
        from django.core.management import call_command
        from io import StringIO
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        out = StringIO()
        
        # Run command for specific user
        call_command('populate_activity_feeds', user_id=self.user.id, stdout=out)
        
        # Check output
        output = out.getvalue()
        self.assertIn(f'Processing user: {self.user.username}', output)
        self.assertIn('Successfully created 1 activity feed items', output)
    
    def test_cleanup_activity_feeds_command(self):
        """Test cleanup_activity_feeds management command."""
        from django.core.management import call_command
        from django.utils import timezone
        from datetime import timedelta
        from io import StringIO
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Create old read item
        old_date = timezone.now() - timedelta(days=100)
        old_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_reminder',
            title='Old Item',
            description='Old description',
            is_read=True
        )
        # Update the created_at timestamp to be old
        old_item.created_at = old_date
        old_item.save()
        
        out = StringIO()
        
        # Run command
        call_command('cleanup_activity_feeds', stdout=out)
        
        # Check output
        output = out.getvalue()
        self.assertIn('Successfully deleted 1 items total', output)
        
        # Check that old item was deleted
        self.assertFalse(UserActivityFeed.objects.filter(id=old_item.id).exists())
    
    def test_cleanup_activity_feeds_command_dry_run(self):
        """Test cleanup_activity_feeds command with dry run."""
        from django.core.management import call_command
        from django.utils import timezone
        from datetime import timedelta
        from io import StringIO
        
        # Clear any existing feed items
        UserActivityFeed.objects.filter(user=self.user).delete()
        
        # Create old read item
        old_date = timezone.now() - timedelta(days=100)
        old_item = UserActivityFeed.objects.create(
            user=self.user,
            activity_type='fast_reminder',
            title='Old Item',
            description='Old description',
            is_read=True
        )
        # Update the created_at timestamp to be old
        old_item.created_at = old_date
        old_item.save()
        
        out = StringIO()
        
        # Run command with dry run
        call_command('cleanup_activity_feeds', dry_run=True, stdout=out)
        
        # Check output
        output = out.getvalue()
        self.assertIn('DRY RUN: Would delete 1 items total', output)
        
        # Check that old item still exists (dry run)
        self.assertTrue(UserActivityFeed.objects.filter(id=old_item.id).exists())
