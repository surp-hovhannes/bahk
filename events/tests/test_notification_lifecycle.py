"""Notification vocabulary and registration; no sender or endpoint behavior."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from events.event_schema import (
    ACTION_EVENT_CODES, EVENT_CODE_CLASSIFICATION, MEANINGFUL_EVENT_CODES,
    NOTIFICATION_DESTINATIONS, NOTIFICATION_KINDS, NOTIFICATION_LIFECYCLE_EVENT_CODES,
    NOTIFICATION_OUTCOMES, NOTIFICATION_PAYLOAD_KEYS, NOTIFICATION_REPORTING_GROUPS,
)
from events.models import Event, EventType, UserActivityFeed
from events.participation_analytics import analytics_events
from tests.base import BaseTestCase


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
    USE_ASYNC_ACTIVITY_FEED=False,
)
class NotificationLifecycleTests(BaseTestCase):
    def test_registration_defaults_and_existing_rows_are_preserved(self):
        EventType.objects.filter(code__in=NOTIFICATION_LIFECYCLE_EVENT_CODES).delete()
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
        for code in NOTIFICATION_LIFECYCLE_EVENT_CODES:
            self.assertEqual(EVENT_CODE_CLASSIFICATION[code], 'exposure-noise')
            self.assertFalse(NOTIFICATION_PAYLOAD_KEYS[code] & {'message', 'body', 'token', 'data'})

    def test_vocabulary_and_reporting_groups(self):
        self.assertEqual(NOTIFICATION_KINDS, frozenset({
            'upcoming_fast', 'ongoing_fast', 'daily_fast', 'culmination_feast',
            'weekly_prayer_requests', 'prayer_request_completed', 'prayer_thanks',
            'fast_milestone', 'post_fast', 'nudge_nonjoin', 'nudge_inactive',
            'nudge_activity_feed', 'nudge_prayer_acceptance', 'admin_manual', 'test', 'unclassified',
        }))
        self.assertEqual(NOTIFICATION_DESTINATIONS, frozenset({
            'fast', 'devotional', 'prayer_set', 'prayer_request', 'prayer_requests',
            'learn', 'none', 'other',
        }))
        self.assertEqual(NOTIFICATION_OUTCOMES, {
            'provider_accepted', 'provider_rejected', 'send_error',
        })
        self.assertEqual(set(NOTIFICATION_REPORTING_GROUPS), NOTIFICATION_KINDS)
        for kind, group in NOTIFICATION_REPORTING_GROUPS.items():
            expected = ('prayer_request_notifications'
                        if kind in {'prayer_thanks', 'prayer_request_completed'} else kind)
            self.assertEqual(group, expected)

    def test_shared_aggregates_and_activity_feed_exclude_lifecycle(self):
        user = self.create_user(username='notification-viewer')
        for code in NOTIFICATION_LIFECYCLE_EVENT_CODES:
            # Even an accidentally enabled analytics flag must not leak telemetry.
            EventType.objects.filter(code=code).update(track_in_analytics=True)
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
        self.assertFalse(Event.objects.filter(
            event_type__code__in=NOTIFICATION_LIFECYCLE_EVENT_CODES,
            event_type__category='analytics',
        ).exists())

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
        self.assertIn('OK: all core event types are registered and active.', output.getvalue())
        self.assertEqual(list(EventType.objects.order_by('pk').values()), before)

    def test_check_inactive_is_read_only(self):
        # Check all core codes, not only the notification additions.
        EventType.objects.filter(code=EventType.APP_OPEN).update(is_active=False)
        before = list(EventType.objects.order_by('pk').values())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('init_event_types', check=True, stdout=output)
        self.assertIn('Inactive core event types: app_open', output.getvalue())
        self.assertEqual(list(EventType.objects.order_by('pk').values()), before)
