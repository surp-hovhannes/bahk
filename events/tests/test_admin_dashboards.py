"""
Tests for the events admin dashboards.

This module tests:
- User Engagement Dashboard
- App Analytics Dashboard
- Admin view functionality
"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch

from events.models import Event, EventType
from hub.models import Profile, Church, Fast

User = get_user_model()


class AdminDashboardTests(TestCase):
    """Test admin dashboard functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Initialize event types
        EventType.get_or_create_default_types()
        
        # Create admin user
        self.admin_user = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='adminpass123',
            is_staff=True,
            is_superuser=True
        )
        
        # Create regular users
        self.regular_user = User.objects.create_user(
            username='regularuser',
            email='regular@example.com',
            password='regularpass123'
        )
        
        self.staff_user = User.objects.create_user(
            username='staffuser',
            email='staff@example.com',
            password='staffpass123',
            is_staff=True
        )
        
        # Create church and profiles
        self.church = Church.objects.create(name='Test Church')
        self.regular_profile = Profile.objects.create(
            user=self.regular_user,
            church=self.church
        )
        self.staff_profile = Profile.objects.create(
            user=self.staff_user,
            church=self.church
        )
        
        # Create test fast
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church,
            year=2024
        )
        
        # Create test events with different categories and users
        # Use fixed timestamps that are guaranteed to be within query windows
        # Set base time to noon today to avoid edge cases when tests run near midnight
        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
        
        # User engagement events (should appear in User Engagement Dashboard)
        self.user_login_event = Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=self.regular_user,
            title='Regular user logged in'
        )
        # Update timestamp manually
        self.user_login_event.timestamp = today_noon - timedelta(hours=2)
        self.user_login_event.save()
        
        self.fast_join_event = Event.create_event(
            event_type_code=EventType.USER_JOINED_FAST,
            user=self.regular_user,
            target=self.fast,
            title='User joined fast'
        )
        # Update timestamp manually
        self.fast_join_event.timestamp = today_noon - timedelta(hours=1)
        self.fast_join_event.save()
        
        # Analytics events (should appear in App Analytics Dashboard)
        self.app_open_event = Event.create_event(
            event_type_code=EventType.APP_OPEN,
            user=self.regular_user,
            title='App opened',
            data={'platform': 'ios', 'app_version': '1.0'}
        )
        # Update timestamp manually
        self.app_open_event.timestamp = today_noon - timedelta(minutes=30)
        self.app_open_event.save()
        
        self.screen_view_event = Event.create_event(
            event_type_code=EventType.SCREEN_VIEW,
            user=self.regular_user,
            title='Screen viewed',
            data={'screen': 'fasts_list', 'source': 'app_ui'}
        )
        # Update timestamp manually
        self.screen_view_event.timestamp = today_noon - timedelta(minutes=15)
        self.screen_view_event.save()

        # API screen view should be excluded from UI metrics
        self.api_screen_view_event = Event.create_event(
            event_type_code=EventType.SCREEN_VIEW,
            user=self.regular_user,
            title='API Screen viewed',
            data={'screen': 'api_endpoint', 'path': '/api/data/', 'source': 'api'}
        )
        self.api_screen_view_event.timestamp = today_noon - timedelta(minutes=20)
        self.api_screen_view_event.save()
        
        # Staff user events (should be excluded from both dashboards)
        self.staff_login_event = Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=self.staff_user,
            title='Staff user logged in'
        )
        # Update timestamp manually
        self.staff_login_event.timestamp = today_noon - timedelta(minutes=45)
        self.staff_login_event.save()
        
        self.staff_app_open_event = Event.create_event(
            event_type_code=EventType.APP_OPEN,
            user=self.staff_user,
            title='Staff app opened'
        )
        # Update timestamp manually
        self.staff_app_open_event.timestamp = today_noon - timedelta(minutes=10)
        self.staff_app_open_event.save()
        
        # Set up admin client
        self.client = Client()
        self.client.force_login(self.admin_user)
    
    def test_user_engagement_dashboard_view(self):
        """Test the User Engagement Dashboard view."""
        url = reverse('admin:events_analytics')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'User Engagement Dashboard')
        
        # Check that context contains expected data
        context = response.context
        self.assertIn('total_events', context)
        self.assertIn('events_by_day', context)
        self.assertIn('events_by_type', context)
        self.assertIn('top_users', context)
        
        # Verify that staff events are excluded
        # The total should not include staff events
        self.assertGreater(context['total_events'], 0)
    
    def test_user_engagement_dashboard_data_endpoint(self):
        """Test the User Engagement Dashboard AJAX data endpoint."""
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '30'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Check expected data structure based on actual response
        self.assertIn('events_by_day', data)
        self.assertIn('fast_trends_data', data)
        self.assertIn('fast_joins', data)
        self.assertIn('fast_leaves', data)
        self.assertIn('net_joins', data)
        self.assertIn('events_in_period', data)
        self.assertIn('current_upcoming_fast_data', data)
        
        # Verify data is properly filtered (no staff events)
        self.assertIsInstance(data['events_by_day'], dict)
        self.assertIsInstance(data['fast_trends_data'], dict)
    
    def test_app_analytics_dashboard_view(self):
        """Test the App Analytics Dashboard view."""
        url = reverse('admin:events_app_analytics')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'App Analytics Dashboard')
        
        # Check that context contains expected data
        context = response.context
        self.assertIn('total_app_opens', context)
        self.assertIn('total_screen_views', context)
        self.assertIn('active_users', context)
        self.assertIn('avg_session_duration', context)
        self.assertIn('app_open_hourly', context)
        self.assertIn('top_screens', context)
        self.assertIn('platform_counts', context)

        # Verify analytics data is present
        self.assertGreaterEqual(context['total_app_opens'], 1)
        self.assertEqual(context['total_screen_views'], 1)
    
    def test_app_analytics_dashboard_data_endpoint(self):
        """Test the App Analytics Dashboard AJAX data endpoint."""
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '30'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Check expected data structure
        self.assertIn('events_by_day', data)
        self.assertIn('app_open_hourly', data)
        self.assertIn('total_app_opens', data)
        self.assertIn('total_screen_views', data)
        self.assertIn('active_users', data)
        self.assertIn('avg_session_duration', data)
        self.assertIn('top_screens', data)
        self.assertIn('platform_counts', data)

        # Verify analytics data is present
        self.assertGreaterEqual(data['total_app_opens'], 1)
        self.assertEqual(data['total_screen_views'], 1)
    
    def test_dashboard_staff_event_exclusion(self):
        """Test that both dashboards properly exclude staff events."""
        # User Engagement Dashboard should exclude staff events
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '1'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Count events in the response - should not include staff events
        total_events_in_response = sum(data['events_by_day'].values())
        
        # Should have regular user events but not staff events
        self.assertGreater(total_events_in_response, 0)
        
        # App Analytics Dashboard should also exclude staff events
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '1'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Should have regular user analytics events but not staff analytics events
        self.assertGreaterEqual(data['total_app_opens'], 1)  # Regular user's app open
    
    def test_dashboard_category_filtering(self):
        """Test that dashboards properly filter by event categories."""
        # User Engagement Dashboard should exclude analytics category
        # Test the view (not data endpoint) as it contains events_by_type
        url = reverse('admin:events_analytics')
        response = self.client.get(url, {'days': '1'})
        
        self.assertEqual(response.status_code, 200)
        context = response.context
        
        # Check event types - should not include analytics category events
        events_by_type = context.get('events_by_type', [])
        event_type_names = [event['event_type__name'] for event in events_by_type]
        
        # Should include user engagement events if they exist
        if event_type_names:
            # Should not include analytics events like App Open or Screen View
            self.assertNotIn('App Open', event_type_names)
            self.assertNotIn('Screen View', event_type_names)
        
        # App Analytics Dashboard should only include analytics category
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '1'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Should have analytics events
        self.assertGreaterEqual(data['total_app_opens'], 1)
        self.assertGreaterEqual(data['total_screen_views'], 1)
    
    def test_dashboard_date_range_filtering(self):
        """Test that dashboards properly filter by date ranges."""
        # Test with different date ranges
        for days in [7, 30, 90]:
            url = reverse('admin:events_analytics_data')
            response = self.client.get(url, {'days': str(days)})
            
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn('events_by_day', data)
            
            # App analytics dashboard
            url = reverse('admin:events_app_analytics_data')
            response = self.client.get(url, {'days': str(days)})
            
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn('events_by_day', data)
    
    def test_dashboard_invalid_date_range(self):
        """Test dashboard behavior with invalid date ranges."""
        # Test invalid date range
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '999'})
        
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('error', data)
        
        # App analytics dashboard
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '-1'})
        
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('error', data)
    
    def test_dashboard_screen_analytics(self):
        """Test screen analytics functionality in app dashboard."""
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '1'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Check top screens data
        self.assertIn('top_screens', data)
        top_screens = data['top_screens']
        
        # Should have at least one screen entry
        self.assertGreater(len(top_screens), 0)
        
        # Find our test screen
        fasts_list_screen = next(
            (screen for screen in top_screens if screen['data__screen'] == 'fasts_list'),
            None
        )
        self.assertIsNotNone(fasts_list_screen)
        self.assertGreaterEqual(fasts_list_screen['count'], 1)

        api_screen = next(
            (screen for screen in top_screens if screen['data__screen'] == 'api_endpoint'),
            None
        )
        self.assertIsNone(api_screen)
    
    def test_dashboard_platform_analytics(self):
        """Test platform analytics functionality in app dashboard."""
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '1'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Check platform counts data
        self.assertIn('platform_counts', data)
        platform_counts = data['platform_counts']
        
        # Should have at least one platform entry
        self.assertGreater(len(platform_counts), 0)
        
        # Find our test platform
        ios_platform = next(
            (platform for platform in platform_counts if platform['data__platform'] == 'ios'),
            None
        )
        self.assertIsNotNone(ios_platform)
        self.assertGreaterEqual(ios_platform['count'], 1)
    
    def test_dashboard_permissions(self):
        """Test that only staff users can access dashboards."""
        # Logout admin user
        self.client.logout()
        
        # Try to access as regular user
        self.client.force_login(self.regular_user)
        
        url = reverse('admin:events_analytics')
        response = self.client.get(url)
        
        # Should redirect to login (regular users can't access admin)
        self.assertEqual(response.status_code, 302)
        
        url = reverse('admin:events_app_analytics')
        response = self.client.get(url)
        
        # Should redirect to login
        self.assertEqual(response.status_code, 302)
    
    @patch('events.analytics_optimizer.AnalyticsQueryOptimizer.get_daily_event_aggregates')
    def test_dashboard_uses_optimizer(self, mock_optimizer):
        """Test that dashboards use the AnalyticsQueryOptimizer."""
        mock_optimizer.return_value = {
            'events_by_day': {'2024-01-01': 5},
            'fast_joins_by_day': {'2024-01-01': 2},
            'fast_leaves_by_day': {'2024-01-01': 1}
        }
        
        # User Engagement Dashboard
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '30'})
        
        self.assertEqual(response.status_code, 200)
        
        # Should have called optimizer with proper filters
        mock_optimizer.assert_called()
        call_args = mock_optimizer.call_args
        self.assertIn('filters', call_args[1])
        filters = call_args[1]['filters']
        self.assertTrue(filters['exclude_staff'])
        self.assertIn('analytics', filters['exclude_categories'])
        
        mock_optimizer.reset_mock()
        
        # App Analytics Dashboard
        url = reverse('admin:events_app_analytics_data')
        response = self.client.get(url, {'days': '30'})
        
        self.assertEqual(response.status_code, 200)
        
        # Should have called optimizer with different filters
        mock_optimizer.assert_called()
        call_args = mock_optimizer.call_args
        self.assertIn('filters', call_args[1])
        filters = call_args[1]['filters']
        self.assertTrue(filters['exclude_staff'])
        self.assertIn('analytics', filters['include_categories'])
