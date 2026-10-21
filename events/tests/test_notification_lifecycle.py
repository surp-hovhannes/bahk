"""Notification vocabulary and registration; no sender or endpoint behavior."""

from io import StringIO
from pathlib import Path
import re
from unittest.mock import patch

from rest_framework.test import APIClient

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from events.event_schema import (
    ACTION_EVENT_CODES, EVENT_CODE_CLASSIFICATION, MEANINGFUL_EVENT_CODES,
    NOTIFICATION_DESTINATIONS, NOTIFICATION_KINDS, NOTIFICATION_LIFECYCLE_EVENT_CODES,
    NOTIFICATION_PAYLOAD_KEYS, NOTIFICATION_REPORTING_GROUPS, ROUTE_FAMILIES,
)
from events.models import Event, EventType, UserActivityFeed
from events.participation_analytics import analytics_events
from tests.base import BaseTestCase


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
    USE_ASYNC_ACTIVITY_FEED=False,
)
class NotificationLifecycleTests(BaseTestCase):
    def test_missing_event_type_reports_validation_error(self):
        with self.assertRaises(ValidationError) as error:
            Event(title='Missing type').save()
        self.assertIn('event_type', error.exception.message_dict)

    def test_activity_feed_signal_without_event_type(self):
        from events.signals import create_activity_feed_item

        create_activity_feed_item(Event, Event(title='Missing type'), created=False)

    def test_registration_defaults_and_existing_rows_are_preserved(self):
        EventType.objects.filter(code__in=sorted(NOTIFICATION_LIFECYCLE_EVENT_CODES)).delete()
        created = EventType.get_or_create_default_types()
        self.assertEqual({row.code for row in created}, NOTIFICATION_LIFECYCLE_EVENT_CODES)
        for row in created:
            self.assertEqual(row.category, 'notification')
            self.assertFalse(row.requires_target)
            self.assertFalse(row.track_in_analytics)
        other = EventType.objects.get(code=EventType.APP_OPEN)
        self.assertTrue(other.track_in_analytics)
        row = created[0]
        EventType.objects.filter(pk=row.pk).update(track_in_analytics=True, is_active=False)
        self.assertEqual(EventType.get_or_create_default_types(), [])
        row.refresh_from_db()
        self.assertTrue(row.track_in_analytics)
        self.assertFalse(row.is_active)

    def test_classification_and_payload_contracts(self):
        self.assertEqual(len(NOTIFICATION_LIFECYCLE_EVENT_CODES), 5)
        self.assertEqual(set(NOTIFICATION_PAYLOAD_KEYS), NOTIFICATION_LIFECYCLE_EVENT_CODES)
        self.assertFalse(NOTIFICATION_LIFECYCLE_EVENT_CODES & MEANINGFUL_EVENT_CODES)
        self.assertFalse(NOTIFICATION_LIFECYCLE_EVENT_CODES & ACTION_EVENT_CODES)
        for code in sorted(NOTIFICATION_LIFECYCLE_EVENT_CODES):
            self.assertEqual(EVENT_CODE_CLASSIFICATION[code], 'exposure-noise')
            self.assertFalse(NOTIFICATION_PAYLOAD_KEYS[code] & {'message', 'body', 'token', 'data'})

    def test_vocabulary_and_reporting_groups(self):
        from notifications.constants import NOTIFICATION_TYPE_FILTERS

        self.assertTrue(set(NOTIFICATION_TYPE_FILTERS) <= NOTIFICATION_KINDS)
        self.assertNotIn('post_fast', NOTIFICATION_KINDS)
        self.assertEqual(list(NOTIFICATION_REPORTING_GROUPS), sorted(NOTIFICATION_KINDS))
        self.assertEqual(set(NOTIFICATION_REPORTING_GROUPS), NOTIFICATION_KINDS)
        for kind, group in NOTIFICATION_REPORTING_GROUPS.items():
            expected = ('prayer_request_notifications'
                        if kind in {'prayer_thanks', 'prayer_request_completed'} else kind)
            self.assertEqual(group, expected)

    def test_shared_aggregates_and_activity_feed_exclude_lifecycle(self):
        user = self.create_user(username='notification-viewer')
        for code in sorted(NOTIFICATION_LIFECYCLE_EVENT_CODES):
            # Even an accidentally enabled analytics flag must not leak telemetry.
            EventType.objects.filter(code=code).update(track_in_analytics=True, category='analytics')
            event = Event.create_event(code, user=user, title='Synthetic notification lifecycle')
            self.assertIsNone(UserActivityFeed.create_from_event(event, user))
        kept = [Event.create_event(code, user=user, title='Synthetic activity') for code in (
            EventType.APP_OPEN, EventType.CHECKLIST_USED,
        )]
        kept.append(Event.create_event(
            EventType.SCREEN_VIEW, user=user, title='Synthetic UI screen',
            data={'source': 'client_ui', 'schema_version': 2},
        ))
        Event.create_event(EventType.SCREEN_VIEW, user=user, title='Synthetic legacy screen')
        self.assertEqual(set(analytics_events().filter(user=user).values_list('pk', flat=True)),
                         {event.pk for event in kept})
        self.assertFalse(UserActivityFeed.objects.filter(user=user).exists())
        self.assertNotIn('JOIN "events_eventtype"', str(analytics_events().query))

    def test_check_missing_is_read_only_then_initialization_succeeds(self):
        EventType.objects.filter(code=EventType.NOTIFICATION_SEND).delete()
        before = list(EventType.objects.order_by('pk').values())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('init_event_types', check=True, stdout=output)
        self.assertIn('Missing core event types: notification_send', output.getvalue())
        self.assertEqual(list(EventType.objects.order_by('pk').values()), before)
        self.assertIn(EventType.NOTIFICATION_SEND, EventType.missing_core_types()['missing'])
        EventType.get_or_create_default_types()
        output = StringIO()
        before = list(EventType.objects.order_by('pk').values())
        call_command('init_event_types', check=True, stdout=output)
        self.assertIn('OK: all core event types are registered with valid lifecycle attributes.', output.getvalue())
        self.assertEqual(list(EventType.objects.order_by('pk').values()), before)

    def test_check_inactive_is_read_only(self):
        # Check all core codes, not only the notification additions.
        EventType.objects.filter(code=EventType.APP_OPEN).update(is_active=False)
        before = list(EventType.objects.order_by('pk').values())
        output = StringIO()
        call_command('init_event_types', check=True, stdout=output)
        self.assertIn('Inactive core event types: app_open', output.getvalue())
        self.assertEqual(list(EventType.objects.order_by('pk').values()), before)

    def test_documented_routes_have_non_fallback_families(self):
        docs = (Path(__file__).resolve().parents[2] / 'CLAUDE.md').read_text()
        payloads = docs.split('### Push Notification Payloads', 1)[1].split('**Key Rules**', 1)[0]
        routes = re.findall(r'"screen": "([^"]+)"', payloads)
        self.assertTrue(routes)
        for route in routes:
            family, _, tail = route.partition('/')
            self.assertIn(family, ROUTE_FAMILIES)
            self.assertIsNotNone(re.fullmatch(ROUTE_FAMILIES[family], tail))
            self.assertIn(family.replace('-', '_'), NOTIFICATION_DESTINATIONS - {'other'})
        self.assertIn('"url":', payloads)
        self.assertIn('url', NOTIFICATION_DESTINATIONS - {'other'})

    def test_lifecycle_titles_and_request_metadata_are_private(self):
        from django.test import RequestFactory

        user = self.create_user(username='private@example.com', email='private@example.com')
        request = RequestFactory().get('/', HTTP_USER_AGENT='private agent', REMOTE_ADDR='127.0.0.1')
        for code in EventType.NOTIFICATION_LIFECYCLE_CODES:
            for title in (None, 'private free text'):
                event = Event.create_event(code, user=user, title=title, request=request)
                self.assertEqual(event.title, EventType.NOTIFICATION_LIFECYCLE_TITLES[code])
                self.assertNotIn(user.email, event.title + event.description)
                self.assertNotIn(user.username, event.title + event.description)
                self.assertIsNone(event.ip_address)
                self.assertEqual(event.user_agent, '')

    def test_check_attributes_and_force_preserve_off_switch_and_other_types(self):
        other = EventType.objects.get(code=EventType.APP_OPEN)
        EventType.objects.filter(pk=other.pk).update(track_in_analytics=False)
        before_other = EventType.objects.filter(pk=other.pk).values().get()
        for field, value in (('category', 'analytics'), ('requires_target', True),
                             ('track_in_analytics', True)):
            with self.subTest(field=field):
                EventType.objects.filter(code=EventType.NOTIFICATION_SEND).update(
                    **{field: value, 'is_active': False},
                )
                before = list(EventType.objects.order_by('pk').values())
                output = StringIO()
                with self.assertRaises(CommandError):
                    call_command('init_event_types', check=True, stdout=output)
                self.assertIn(f'notification_send ({field})', output.getvalue())
                self.assertEqual(list(EventType.objects.order_by('pk').values()), before)
                call_command('init_event_types', force=True, stdout=StringIO())
                row = EventType.objects.get(code=EventType.NOTIFICATION_SEND)
                self.assertFalse(row.is_active)
                self.assertEqual(row.category, 'notification')
                self.assertFalse(row.requires_target)
                self.assertFalse(row.track_in_analytics)
                call_command('init_event_types', check=True, stdout=StringIO())
        self.assertEqual(EventType.objects.filter(pk=other.pk).values().get(), before_other)
        with self.assertRaises(CommandError):
            call_command('init_event_types', check=True, force=True, stdout=StringIO())

    @override_settings(USE_ASYNC_ACTIVITY_FEED=True)
    def test_lifecycle_skips_save_side_effects_other_types_keep_them(self):
        user = self.create_user(username='side-effects')
        with patch('events.analytics_cache.AnalyticsCacheService.invalidate_current_day') as invalidate, \
                patch('events.tasks.create_activity_feed_item_task.delay') as delay:
            for code in EventType.NOTIFICATION_LIFECYCLE_CODES:
                Event.create_event(code, user=user)
            invalidate.assert_not_called()
            delay.assert_not_called()
            event = Event.create_event(EventType.APP_OPEN, user=user, title='App opened')
            invalidate.assert_called_once_with()
            delay.assert_called_once_with(event.pk, user.pk)

    def test_my_events_hides_lifecycle(self):
        user = self.create_user(username='my-events')
        hidden = Event.create_event(EventType.NOTIFICATION_SEND, user=user)
        visible = Event.create_event(EventType.APP_OPEN, user=user, title='App opened')
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get('/api/events/events/my/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        ids = {row['id'] for row in rows}
        self.assertIn(visible.pk, ids)
        self.assertNotIn(hidden.pk, ids)

    def test_lifecycle_skips_synchronous_feed_dispatch(self):
        user = self.create_user(username='sync-feed')
        with patch('events.models.UserActivityFeed.create_from_event') as create:
            Event.create_event(EventType.NOTIFICATION_SEND, user=user)
            create.assert_not_called()
            event = Event.create_event(EventType.APP_OPEN, user=user, title='App opened')
            create.assert_called_once_with(event, user)

    def test_backfills_skip_lifecycle_before_feed_existence_checks(self):
        from events.tasks import populate_user_activity_feed_task

        user = self.create_user(username='backfill')
        Event.create_event(EventType.NOTIFICATION_SEND, user=user)
        with patch('events.models.UserActivityFeed.objects.filter') as feed_filter:
            self.assertEqual(populate_user_activity_feed_task(user.pk), 0)
            call_command('populate_activity_feeds', user_id=user.pk, stdout=StringIO())
            feed_filter.assert_not_called()
