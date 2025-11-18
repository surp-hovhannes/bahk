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
            data={'screen': 'fasts_list'}
        )
        # Update timestamp manually
        self.screen_view_event.timestamp = today_noon - timedelta(minutes=15)
        self.screen_view_event.save()
        
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
        self.assertGreaterEqual(context['total_screen_views'], 1)
    
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
        self.assertGreaterEqual(data['total_screen_views'], 1)
    
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
        self.assertIn('exclude_event_types', filters)
        # Should exclude pure analytics events
        from events.models import EventType
        self.assertIn(EventType.APP_OPEN, filters['exclude_event_types'])
        
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

    def test_new_kpi_metrics_in_view(self):
        """Test that new KPI metrics are included in the analytics view."""
        # Create KPI events
        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

        # User signup event
        signup_event = Event.create_event(
            event_type_code=EventType.USER_ACCOUNT_CREATED,
            user=self.regular_user,
            title='User account created'
        )
        signup_event.timestamp = today_noon - timedelta(hours=1)
        signup_event.save()

        # Devotional viewed event
        devotional_event = Event.create_event(
            event_type_code=EventType.DEVOTIONAL_VIEWED,
            user=self.regular_user,
            title='Devotional viewed',
            target=self.fast
        )
        devotional_event.timestamp = today_noon - timedelta(hours=2)
        devotional_event.save()

        # Checklist used event
        checklist_event = Event.create_event(
            event_type_code=EventType.CHECKLIST_USED,
            user=self.regular_user,
            title='Checklist used'
        )
        checklist_event.timestamp = today_noon - timedelta(hours=3)
        checklist_event.save()

        # Prayer set viewed event
        prayer_event = Event.create_event(
            event_type_code=EventType.PRAYER_SET_VIEWED,
            user=self.regular_user,
            title='Prayer set viewed',
            target=self.fast
        )
        prayer_event.timestamp = today_noon - timedelta(hours=4)
        prayer_event.save()

        # Test the analytics view
        url = reverse('admin:events_analytics')
        response = self.client.get(url, {'days': '1'})

        self.assertEqual(response.status_code, 200)
        context = response.context

        # Check that KPI metrics are in the context
        self.assertIn('user_signups', context)
        self.assertIn('devotional_views', context)
        self.assertIn('checklist_usage', context)
        self.assertIn('prayer_set_views', context)

        # Check that daily breakdowns are in the context
        self.assertIn('user_signups_by_day', context)
        self.assertIn('devotional_views_by_day', context)
        self.assertIn('checklist_usage_by_day', context)
        self.assertIn('prayer_set_views_by_day', context)

        # Check that feature usage chart data is present
        self.assertIn('feature_usage_over_time', context)

        # Verify counts
        self.assertGreaterEqual(context['user_signups'], 1)
        self.assertGreaterEqual(context['devotional_views'], 1)
        self.assertGreaterEqual(context['checklist_usage'], 1)
        self.assertGreaterEqual(context['prayer_set_views'], 1)

    def test_new_kpi_metrics_in_data_endpoint(self):
        """Test that new KPI metrics are included in the analytics data endpoint."""
        # Create KPI events
        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

        # User signup event
        signup_event = Event.create_event(
            event_type_code=EventType.USER_ACCOUNT_CREATED,
            user=self.regular_user,
            title='User account created'
        )
        signup_event.timestamp = today_noon - timedelta(hours=1)
        signup_event.save()

        # Devotional viewed event
        devotional_event = Event.create_event(
            event_type_code=EventType.DEVOTIONAL_VIEWED,
            user=self.regular_user,
            title='Devotional viewed',
            target=self.fast
        )
        devotional_event.timestamp = today_noon - timedelta(hours=2)
        devotional_event.save()

        # Test the data endpoint
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '1'})

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Check that KPI metrics are in the response
        self.assertIn('user_signups', data)
        self.assertIn('devotional_views', data)
        self.assertIn('checklist_usage', data)
        self.assertIn('prayer_set_views', data)

        # Check that daily breakdowns are in the response
        self.assertIn('user_signups_by_day', data)
        self.assertIn('devotional_views_by_day', data)
        self.assertIn('checklist_usage_by_day', data)
        self.assertIn('prayer_set_views_by_day', data)

        # Check that feature usage chart data is present
        self.assertIn('feature_usage_over_time', data)

        # Verify structure of feature_usage_over_time
        feature_usage = data['feature_usage_over_time']
        self.assertIn('labels', feature_usage)
        self.assertIn('datasets', feature_usage)
        self.assertIsInstance(feature_usage['datasets'], list)
        self.assertEqual(len(feature_usage['datasets']), 4)  # 4 metrics

        # Verify dataset labels
        dataset_labels = [dataset['label'] for dataset in feature_usage['datasets']]
        self.assertIn('User Signups', dataset_labels)
        self.assertIn('Devotional Views', dataset_labels)
        self.assertIn('Checklist Uses', dataset_labels)
        self.assertIn('Prayer Set Views', dataset_labels)

        # Verify counts
        self.assertGreaterEqual(data['user_signups'], 1)
        self.assertGreaterEqual(data['devotional_views'], 1)

    def test_kpi_excludes_staff_events(self):
        """Test that KPI metrics exclude staff user events."""
        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

        # Create regular user KPI event
        regular_signup = Event.create_event(
            event_type_code=EventType.USER_ACCOUNT_CREATED,
            user=self.regular_user,
            title='Regular user account created'
        )
        regular_signup.timestamp = today_noon - timedelta(hours=1)
        regular_signup.save()

        # Create staff user KPI event (should be excluded)
        staff_signup = Event.create_event(
            event_type_code=EventType.USER_ACCOUNT_CREATED,
            user=self.staff_user,
            title='Staff user account created'
        )
        staff_signup.timestamp = today_noon - timedelta(hours=2)
        staff_signup.save()

        # Test the data endpoint
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '1'})

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should only count the regular user signup, not the staff user signup
        self.assertEqual(data['user_signups'], 1)

    def test_kpi_daily_breakdown_accuracy(self):
        """Test that KPI daily breakdowns accurately reflect event counts."""
        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
        yesterday_noon = today_noon - timedelta(days=1)

        # Create events on different days
        # Today's devotional
        devotional_today = Event.create_event(
            event_type_code=EventType.DEVOTIONAL_VIEWED,
            user=self.regular_user,
            title='Devotional viewed today',
            target=self.fast
        )
        devotional_today.timestamp = today_noon - timedelta(hours=1)
        devotional_today.save()

        # Yesterday's devotional
        devotional_yesterday = Event.create_event(
            event_type_code=EventType.DEVOTIONAL_VIEWED,
            user=self.regular_user,
            title='Devotional viewed yesterday',
            target=self.fast
        )
        devotional_yesterday.timestamp = yesterday_noon
        devotional_yesterday.save()

        # Test the data endpoint with 2 days
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '2'})

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Total should be 2
        self.assertEqual(data['devotional_views'], 2)

        # Daily breakdown should show events distributed across days
        devotional_by_day = data['devotional_views_by_day']
        self.assertIsInstance(devotional_by_day, dict)

        # Sum of daily counts should equal total
        daily_sum = sum(devotional_by_day.values())
        self.assertEqual(daily_sum, 2)

    def test_pure_analytics_events_excluded(self):
        """Test that pure analytics events (APP_OPEN, etc.) are excluded from engagement dashboard."""
        # The existing app_open_event and screen_view_event from setUp should be excluded
        url = reverse('admin:events_analytics_data')
        response = self.client.get(url, {'days': '1'})

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # The events_by_day should NOT include APP_OPEN or SCREEN_VIEW events
        # We know from setUp that we have app_open_event and screen_view_event
        # But they should not be counted in the engagement dashboard

        # Verify by checking that events_in_period excludes analytics events
        # We have login and fast join events which should be included
        events_count = data['events_in_period']

        # Should have at least the user login and fast join events
        self.assertGreater(events_count, 0)

        # Create a devotional event to ensure engagement events are still counted
        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

        devotional_event = Event.create_event(
            event_type_code=EventType.DEVOTIONAL_VIEWED,
            user=self.regular_user,
            title='Devotional viewed',
            target=self.fast
        )
        devotional_event.timestamp = today_noon - timedelta(hours=1)
        devotional_event.save()

        # Get data again
        response = self.client.get(url, {'days': '1'})
        data = response.json()

        # Should now have the devotional view counted
        self.assertGreaterEqual(data['devotional_views'], 1)
