"""
Tests for the events admin dashboards.

This module tests:
- User Engagement Dashboard
- App Analytics Dashboard
- Admin view functionality
"""

from django.conf import settings
from django.test import TestCase, Client, override_settings
from django.core.cache import cache, caches
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch
from queue import Empty

from events.engagement_analytics import (
    LEASE_TTL,
    build_engagement_snapshot,
    engagement_window,
    snapshot_key,
)
from events.models import Event, EventType
from events.tasks import refresh_engagement_snapshot
from hub.models import Profile, Church, Fast, Day, Devotional
from tests.fixtures.test_data import TestDataFactory

User = get_user_model()


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
    SESSION_ENGINE='django.contrib.sessions.backends.db',
)
class AdminDashboardTests(TestCase):
    """Test admin dashboard functionality."""
    
    def setUp(self):
        """Set up test data."""
        # These metric assertions exercise the real builder without a broker.
        def computed_snapshot(days, end_date):
            snapshot = build_engagement_snapshot(days, end_date - timedelta(days=days), end_date)
            return snapshot, 200

        snapshot_mock = patch('events.admin.get_engagement_snapshot', side_effect=computed_snapshot)
        snapshot_mock.start()
        self.addCleanup(snapshot_mock.stop)

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
            data={'screen': 'fasts_list', 'source': 'client_ui'}
        )
        # Update timestamp manually
        self.screen_view_event.timestamp = today_noon - timedelta(minutes=15)
        self.screen_view_event.save()

        # Legacy API screen view must be excluded from screen metrics
        self.api_screen_view_event = Event.create_event(
            event_type_code=EventType.SCREEN_VIEW,
            user=self.regular_user,
            title='API Screen viewed',
            data={'screen': '/api/private/?secret=objectionable', 'path': '/api/data/', 'source': 'api'}
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
        self.assertEqual(context['site_header'], 'Fast & Pray Admin')
        self.assertEqual(context['opts'], Event._meta)
        self.assertTrue(context['has_view_permission'])
        self.assertContains(response, 'admin/css/fastandpray-admin.css')
        self.assertContains(response, 'admin/css/fastandpray-analytics.css')
        self.assertContains(response, '<label for="dateRange">Date range</label>', html=True)
        self.assertContains(response, 'type="button" class="stat-card"', count=4)
        self.assertContains(response, 'role="img" aria-label=', count=7)
        self.assertEqual(response.content.decode().count('User Engagement Dashboard</h1>'), 1)
        
        # Verify that staff events are excluded
        # The total should not include staff events
        self.assertGreater(context['total_events'], 0)

    def test_user_engagement_dashboard_binds_controls_before_chart_setup(self):
        """Chart enhancement errors must not disable filters or detail dialogs."""
        response = self.client.get(reverse('admin:events_analytics'))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        controls_binding = content.rindex('    bindAnalyticsControls();')
        chart_initialization = content.rindex('    initializeCharts();')

        self.assertLess(controls_binding, chart_initialization)
        self.assertIn(
            'chart.options.plugins.legend.labels.color = chartTheme.text;',
            content,
        )
        self.assertNotIn(
            'chart.options.plugins.legend.labels = {',
            content,
        )
    
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
        self.assertEqual(context['site_header'], 'Fast & Pray Admin')
        self.assertEqual(context['opts'], Event._meta)
        self.assertTrue(context['has_view_permission'])
        self.assertContains(response, 'admin/css/fastandpray-admin.css')
        self.assertContains(response, 'admin/css/fastandpray-analytics.css')
        self.assertContains(response, '<label for="dateRange">Date range</label>', html=True)
        self.assertContains(response, 'role="img" aria-label=', count=5)
        self.assertEqual(response.content.decode().count('App Analytics Dashboard</h1>'), 1)

        # Verify analytics data is present
        self.assertGreaterEqual(context['total_app_opens'], 1)
        self.assertEqual(context['total_screen_views'], 1)
        self.assertEqual(context['top_screens'], [{'data__screen': 'fasts_list', 'count': 1}])
    
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
        self.assertEqual(data['top_screens'], [{'data__screen': 'fasts_list', 'count': 1}])
    
    def test_app_analytics_active_users_ignore_legacy_screen_views(self):
        """Active users share the screen metrics' population: legacy rows count nowhere."""
        data_url = reverse('admin:events_app_analytics_data')
        page_url = reverse('admin:events_app_analytics')
        baseline = self.client.get(data_url, {'days': '30'}).json()['active_users']
        legacy_only = User.objects.create_user(username='legacy_only', email='legacy_only@example.com')
        Event.objects.create(
            event_type=EventType.objects.get(code=EventType.SCREEN_VIEW), user=legacy_only,
            title='Legacy screen view', data={'screen': '/api/fasts/', 'source': 'api'},
        )
        self.assertEqual(self.client.get(data_url, {'days': '30'}).json()['active_users'], baseline)
        self.assertEqual(self.client.get(page_url, {'days': '30'}).context['active_users'], baseline)

        Event.create_event(
            event_type_code=EventType.SCREEN_VIEW, user=legacy_only, title='UI screen view',
            data={'screen': 'home', 'source': 'client_ui'},
        )
        self.assertEqual(self.client.get(data_url, {'days': '30'}).json()['active_users'], baseline + 1)

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
            (screen for screen in top_screens if screen['data__screen'] == '/api/private/?secret=objectionable'),
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

    def test_engagement_drilldowns_accept_custom_days_and_share_window(self):
        start, end = engagement_window(14)
        self.regular_user.date_joined = start
        self.regular_user.save(update_fields=['date_joined'])
        for code, data in (
            (EventType.DEVOTIONAL_VIEWED, {'devotional_id': 1}),
            (EventType.CHECKLIST_USED, {'action': 'item_toggled_prayer'}),
            (EventType.PRAYER_SET_VIEWED, {'prayer_set_id': 1}),
        ):
            event_type = EventType.objects.get(code=code)
            for timestamp in (start - timedelta(seconds=1), start, end):
                event = Event.objects.create(
                    event_type=event_type, title=code, user=self.regular_user,
                    target=self.fast, data=data,
                )
                Event.objects.filter(pk=event.pk).update(timestamp=timestamp)

        page = self.client.get(reverse('admin:events_analytics'), {'days': 14})
        self.assertContains(page, '<option value="14" selected>')
        self.assertEqual((page.context['start_date'], page.context['end_date']), (start, end))

        for name, count_field in (
            ('new_users', None),
            ('devotional_views', 'views'),
            ('checklist_usage', 'usage_count'),
            ('checklist_items', 'times_checked'),
            ('prayer_views', 'views'),
        ):
            with self.subTest(endpoint=name):
                url = reverse(f'admin:events_{name}_data')
                response = self.client.get(url, {'days': 14})
                self.assertEqual(response.status_code, 200)
                items = response.json()['items']
                self.assertEqual(len(items), 1)
                if count_field:
                    self.assertEqual(items[0][count_field], 1)
                else:
                    self.assertEqual(items[0]['id'], self.regular_user.id)
                self.assertEqual(self.client.get(url, {'days': 1}).json()['items'], [])
                self.assertEqual(self.client.get(url, {'days': 365}).status_code, 200)
                for invalid in ('bad', '0', '366', '9' * 17):
                    self.assertEqual(self.client.get(url, {'days': invalid}).status_code, 400)

    def test_new_users_modal_includes_location(self):
        """User signups modal should include profile location."""
        self.regular_profile.location = 'New York, NY'
        self.regular_profile.save(update_fields=['location'])

        url = reverse('admin:events_new_users_data')
        response = self.client.get(url, {'days': '30', 'sort': 'recent_first', 'offset': '0', 'limit': '50'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('items', data)

        # Find the regular user row
        row = next((u for u in data['items'] if u['id'] == self.regular_user.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row.get('location'), 'New York, NY')

    def test_devotional_views_modal_enriches_fast_and_title(self):
        """Devotional views modal should show fast name and devotional title."""
        video = TestDataFactory.create_video(title='Test Devotional Video', category='devotional')
        day = Day.objects.create(date=timezone.now().date(), fast=self.fast, church=self.church)
        devotional = Devotional.objects.create(day=day, video=video, order=1, language_code='en')

        now = timezone.now()
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
        devotional_event = Event.create_event(
            event_type_code=EventType.DEVOTIONAL_VIEWED,
            user=self.regular_user,
            title='Devotional viewed',
            target=devotional,
            data={'devotional_id': devotional.id},
        )
        devotional_event.timestamp = today_noon - timedelta(hours=1)
        devotional_event.save()

        url = reverse('admin:events_devotional_views_data')
        response = self.client.get(url, {'days': '30', 'sort': 'most_viewed', 'offset': '0', 'limit': '20'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('items', data)
        self.assertGreaterEqual(len(data['items']), 1)

        row = next((i for i in data['items'] if i['devotional_id'] == devotional.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row['fast_name'], self.fast.name)
        self.assertEqual(row['title'], video.title)

    def test_event_type_distribution_includes_prayer_types(self):
        """Event distribution should always include prayer-related event types."""
        url = reverse('admin:events_analytics')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        events_by_type = response.context.get('events_by_type', [])
        codes = {row['event_type__code'] for row in events_by_type}

        self.assertIn(EventType.PRAYER_REQUEST_CREATED, codes)
        self.assertIn(EventType.PRAYER_REQUEST_ACCEPTED, codes)
        self.assertIn(EventType.PRAYER_REQUEST_COMPLETED, codes)
        self.assertIn(EventType.PRAYER_REQUEST_THANKS_SENT, codes)
        self.assertIn(EventType.PRAYER_SET_VIEWED, codes)
    
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

    @override_settings(
        TIME_ZONE='America/Los_Angeles',
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'feature-usage-reporting-timezone',
            }
        },
    )
    def test_feature_usage_buckets_follow_reporting_timezone(self):
        """Both chart paths retain boundary events in the window's calendar day."""
        self.client.force_login(self.admin_user)
        feature_types = [
            (EventType.USER_ACCOUNT_CREATED, 'User Signups'),
            (EventType.DEVOTIONAL_VIEWED, 'Devotional Views'),
            (EventType.CHECKLIST_USED, 'Checklist Uses'),
            (EventType.PRAYER_SET_VIEWED, 'Prayer Set Views'),
            (EventType.PRAYER_REQUEST_CREATED, 'Prayer Requests'),
        ]
        event_ids = []
        for code, _ in feature_types:
            event = Event.objects.create(
                event_type=EventType.objects.get(code=code),
                user=self.regular_user,
                title='Boundary event',
                target=self.fast,
            )
            event_ids.append(event.pk)

        for window_tz in [dt_timezone.utc, ZoneInfo('Asia/Tokyo')]:
            # Local 00:30 falls on the previous date in Los Angeles.
            boundary = datetime(2025, 1, 15, 0, 30, tzinfo=window_tz)
            Event.objects.filter(pk__in=event_ids).update(timestamp=boundary)
            expected_day = '2025-01-15'
            now = datetime(2025, 1, 15, 12, tzinfo=window_tz)
            for endpoint in ['events_analytics', 'events_analytics_data']:
                for days in [1, 2]:
                    with self.subTest(timezone=window_tz, endpoint=endpoint, days=days):
                        with timezone.override('America/Los_Angeles'), patch(
                            'events.admin.timezone.now', return_value=now
                        ):
                            response = self.client.get(reverse(f'admin:{endpoint}'), {'days': days})
                        self.assertEqual(response.status_code, 200)
                        data = response.json() if endpoint.endswith('_data') else response.context
                        chart = data['feature_usage_over_time']
                        expected_labels = ['2025-01-14', expected_day] if days == 2 else [expected_day]
                        self.assertEqual(chart['labels'], expected_labels)
                        datasets = {dataset['label']: dataset['data'] for dataset in chart['datasets']}
                        for _, label in feature_types:
                            self.assertEqual(datasets[label], [0, 1] if days == 2 else [1])

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
        self.assertIn('prayer_request_activity', context)

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
        self.assertIn('prayer_request_activity', data)

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
        self.assertEqual(len(feature_usage['datasets']), 5)  # 5 metrics

        # Verify dataset labels
        dataset_labels = [dataset['label'] for dataset in feature_usage['datasets']]
        self.assertIn('User Signups', dataset_labels)
        self.assertIn('Devotional Views', dataset_labels)
        self.assertIn('Checklist Uses', dataset_labels)
        self.assertIn('Prayer Set Views', dataset_labels)
        self.assertIn('Prayer Requests', dataset_labels)

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


@override_settings(
    CACHES={'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'engagement-snapshot-tests',
    }},
    SESSION_ENGINE='django.contrib.sessions.backends.db',
)
class EngagementSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.page = reverse('admin:events_analytics')
        self.data = reverse('admin:events_analytics_data')
        self.admin = User.objects.create(username='engagement-admin', is_staff=True, is_superuser=True)
        self.other_staff = User.objects.create(username='engagement-staff', is_staff=True)
        self.client.force_login(self.admin)
        publish_mock = patch.object(refresh_engagement_snapshot.app, 'send_task')
        self.publish = publish_mock.start()
        self.addCleanup(publish_mock.stop)
        self.key = snapshot_key(30, engagement_window(30)[1])

    def test_cold_and_warm_requests_share_snapshot_without_event_queries(self):
        with CaptureQueriesContext(connection) as queries:
            cold = self.client.get(self.page)
            pending = self.client.get(self.data, {'days': '030'})
        self.assertEqual(cold.status_code, 202)
        self.assertContains(cold, 'Preparing analytics', status_code=202)
        self.assertEqual(pending.status_code, 202)
        self.assertEqual(cold.context['poll_seconds'], pending.json()['poll_seconds'])
        self.assertEqual(pending.json(), {'status': 'pending', 'poll_seconds': 2 * LEASE_TTL + 4})
        self.publish.assert_called_once()
        self.assertEqual(self.publish.call_args.args, (refresh_engagement_snapshot.name,))
        self.assertEqual(self.publish.call_args.kwargs['args'], (30, self.key))
        self.assertFalse(any(
            query['sql'].startswith('SELECT') and '"events_event"' in query['sql']
            for query in queries
        ))

        standard_user = User.objects.create(username='engagement-regular')
        event_type = EventType.objects.create(code=EventType.DEVOTIONAL_VIEWED, name='Devotional')
        Event.objects.create(event_type=event_type, title='Reading', user=standard_user, data={'lang': 'hy'})
        Event.objects.create(event_type=event_type, title='Reading', user=standard_user, data={'lang': 'hy'})
        Event.objects.create(event_type=event_type, title='Staff reading', user=self.admin, data={'lang': 'en'})
        milestone_type = EventType.objects.create(
            code=EventType.FAST_PARTICIPANT_MILESTONE, name='Milestone',
        )
        Event.objects.create(event_type=milestone_type, title='Snapshot milestone', user=standard_user)
        refresh_engagement_snapshot.run(*self.publish.call_args.kwargs['args'])

        self.client.force_login(self.other_staff)
        with CaptureQueriesContext(connection) as queries:
            warm = self.client.get(self.page)
            data = self.client.get(self.data).json()
        self.assertEqual(warm.status_code, 200)
        self.assertEqual(warm.context['poll_seconds'], cold.context['poll_seconds'])
        self.assertFalse(warm.context['has_view_permission'])
        self.assertEqual(warm.context['user'], self.other_staff)
        self.assertEqual(warm.context['total_events'], 3)
        self.assertContains(warm, 'Snapshot milestone')
        self.assertContains(warm, 'engagement-regular')
        self.assertTrue(warm.context['milestones'][0]['timestamp'])
        self.assertEqual(warm.context['top_users'][0]['event_count'], 3)
        self.assertEqual(warm.context['devotional_views'], 2)
        self.assertEqual(data['devotional_views'], 2)
        self.assertEqual(data['devotional_views_by_day'], warm.context['devotional_views_by_day'])
        self.assertEqual(data['language_en'], 0)
        self.assertEqual(data['language_hy'], 1)
        self.assertNotIn('top_users', data)
        self.assertIn('no-store', warm.headers['Cache-Control'])
        self.assertFalse(any(
            query['sql'].startswith('SELECT') and '"events_event"' in query['sql']
            for query in queries
        ))
        self.publish.assert_called_once()

        self.client.force_login(standard_user)
        self.assertEqual(self.client.get(self.page).status_code, 302)
        self.assertEqual(self.client.get(self.data).status_code, 302)

    def test_invalid_ranges_do_not_queue_work(self):
        for endpoint in (self.page, self.data):
            for days in ('bad', '0', '-1', '366', '9' * 5000):
                with self.subTest(endpoint=endpoint, days=days[:10]):
                    self.assertEqual(self.client.get(endpoint, {'days': days}).status_code, 400)
        self.publish.assert_not_called()

    def test_publish_failure_is_throttled_without_caching_a_snapshot(self):
        self.publish.side_effect = OSError('broker unavailable')
        self.assertEqual(self.client.get(self.page).status_code, 503)
        self.assertEqual(self.client.get(self.data).status_code, 202)
        self.assertIsNone(cache.get(self.key))
        self.publish.assert_called_once()

    def test_failed_worker_can_retry_after_lease_expires(self):
        self.assertEqual(self.client.get(self.data).status_code, 202)
        with patch('events.tasks.build_engagement_snapshot', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                refresh_engagement_snapshot.run(30, self.key)
        self.assertIsNone(cache.get(self.key))
        with patch('django.core.cache.backends.locmem.time') as clock:
            clock.time.return_value = datetime.now(dt_timezone.utc).timestamp() + LEASE_TTL + 1
            self.assertEqual(self.client.get(self.data).status_code, 202)
        self.assertEqual(self.publish.call_count, 2)

    def test_previous_calendar_window_is_discarded(self):
        yesterday_key = snapshot_key(30, engagement_window(30)[1] - timedelta(days=1))
        with patch('events.tasks.build_engagement_snapshot') as compute:
            refresh_engagement_snapshot.run(30, yesterday_key)
        compute.assert_not_called()
        self.assertIsNone(cache.get(yesterday_key))

    def test_cache_failure_returns_unavailable_without_queueing(self):
        with patch('events.engagement_analytics.engagement_snapshot_cache') as get_cache:
            snapshot_cache = get_cache.return_value
            snapshot_cache.get.side_effect = OSError('cache unavailable')
            self.assertEqual(self.client.get(self.data).status_code, 503)
        self.publish.assert_not_called()

    def test_snapshot_pool_exhaustion_returns_unavailable_without_queueing(self):
        from bahk.settings import CACHES as redis_caches
        from django_redis.cache import RedisCache

        default_config = redis_caches['default']
        default_cache = RedisCache(default_config['LOCATION'], default_config)
        with self.settings(CACHES={
            **settings.CACHES,
            'engagement_snapshot': redis_caches['engagement_snapshot'],
        }):
            snapshot_cache = caches['engagement_snapshot']
            default_pool = default_cache.client.get_client().connection_pool
            snapshot_pool = snapshot_cache.client.get_client().connection_pool
            self.assertIsNot(default_pool, snapshot_pool)
            self.assertEqual(default_cache.make_key(self.key), snapshot_cache.make_key(self.key))
            self.assertEqual(snapshot_pool.connection_kwargs['socket_connect_timeout'], 1)
            self.assertEqual(snapshot_pool.connection_kwargs['socket_timeout'], 1)
            self.assertEqual(snapshot_pool.connection_kwargs['retry'].get_retries(), 0)
            if default_config['LOCATION'].startswith('rediss://'):
                self.assertIsNone(snapshot_pool.connection_kwargs['ssl_cert_reqs'])
                self.assertFalse(snapshot_pool.connection_kwargs['ssl_check_hostname'])
            with patch.object(snapshot_pool.pool, 'get', side_effect=Empty) as acquire:
                self.assertEqual(self.client.get(self.data).status_code, 503)
            acquire.assert_called_once_with(block=True, timeout=0.25)
        self.publish.assert_not_called()
