"""Notification ingestion contracts; no sender or provider calls."""
from datetime import timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from events.event_schema import validate_notification_telemetry
from events.middleware import session_cache_key
from events.models import Event, EventType
from tests.base import BaseTestCase


@override_settings(
    NOTIFICATION_TELEMETRY_ENABLED=True,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class NotificationTrackingTests(BaseTestCase):
    def setUp(self):
        cache.clear()
        self.user = self.create_user(username='notification-user')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('events:track-notification')

    def tearDown(self):
        cache.clear()

    def rows(self):
        return Event.objects.filter(event_type__code__in=(
            EventType.NOTIFICATION_PROMPT, EventType.NOTIFICATION_OPENED,
            EventType.NOTIFICATION_DESTINATION,
        ))

    def post(self, payload, **headers):
        return self.client.post(self.url, payload, format='json', **headers)

    def opened(self, **extra):
        return dict(event='opened', notif_id='Abcd1234_efgh-56', entry='tap', app_state='warm', **extra)

    def send(self, user=None):
        return Event.create_event(
            EventType.NOTIFICATION_SEND, user=user or self.user,
            title='Notification sent to push provider',
            data={'notif_id': 'Abcd1234_efgh-56', 'kind': 'daily_fast',
                  'destination': 'fast', 'platform': 'ios', 'outcome': 'provider_accepted'},
        )

    def test_valid_events_and_privacy(self):
        cache.set(session_cache_key(self.user.pk), {'id': 'cached-session'})
        for payload, code, expected in (
            ({'event': 'prompt', 'stage': 'selected', 'choice': 'enable', 'trigger': 'settings'},
             EventType.NOTIFICATION_PROMPT,
             {'stage': 'selected', 'choice': 'enable', 'trigger': 'settings'}),
            (self.opened(), EventType.NOTIFICATION_OPENED,
             {'notif_id': 'Abcd1234_efgh-56', 'entry': 'tap', 'app_state': 'warm',
              'matched': False, 'kind': 'unclassified'}),
            ({'event': 'destination', 'notif_id': 'Abcd1234_efgh-56', 'destination': 'fast', 'result': 'opened'},
             EventType.NOTIFICATION_DESTINATION,
             {'notif_id': 'Abcd1234_efgh-56', 'destination': 'fast', 'result': 'opened', 'matched': False}),
        ):
            with self.subTest(code=code):
                before = self.rows().count()
                payload.update(token='private', device_token_id=12, title='private', body='private',
                               data={'private': 'private'}, navigation='private', source='forged', schema_version=1)
                response = self.post(payload, HTTP_X_APP_VERSION='1.2.0+4', HTTP_X_PLATFORM='ios',
                                     REMOTE_ADDR='192.0.2.1', HTTP_USER_AGENT='private')
                self.assertEqual((response.status_code, response.data), (200, {'status': 'ok'}))
                self.assertEqual(self.rows().count(), before + 1)
                row = self.rows().get(event_type__code=code)
                self.assertEqual(row.data, dict(expected, source='client_ui', schema_version=2,
                                                session_id='cached-session', app_version='1.2.0+4', platform='ios'))
                self.assertIsNone(row.ip_address)
                self.assertEqual(row.user_agent, '')

    def test_prompt_combinations(self):
        for stage, choices in (('shown', (None,)), ('selected', ('enable', 'not_now')),
                               ('os_result', ('granted', 'denied', 'undetermined'))):
            for choice in choices:
                with self.subTest(stage=stage, choice=choice):
                    response = self.post(dict(event='prompt', stage=stage, choice=choice, trigger='first_prayer'))
                    self.assertEqual(response.data, {'status': 'ok'})
                    if stage == 'shown':
                        self.assertNotIn('choice', self.rows().latest('pk').data)

    def test_invalid_payloads(self):
        payloads = [[], 'text', {}, {'event': []}, self.opened() | {'notif_id': 'bad'},
                    self.opened() | {'notif_id': 'Abcd1234_efgh-56\n'},
                    self.opened() | {'notif_id': {}}, self.opened() | {'entry': []},
                    self.opened() | {'app_state': 'active'},
                    self.opened() | {'entry': 'cold_start'},
                    {'event': 'destination', 'notif_id': 'Abcd1234_efgh-56', 'destination': 'none', 'result': 'opened'},
                    {'event': 'destination', 'notif_id': 'Abcd1234_efgh-56', 'destination': 'private', 'result': 'opened'},
                    {'event': 'destination', 'notif_id': 'Abcd1234_efgh-56', 'destination': 'fast', 'result': {}},
                    {'event': 'prompt', 'stage': [], 'trigger': 'settings'},
                    {'event': 'prompt', 'stage': 'shown', 'choice': 'enable', 'trigger': 'settings'},
                    {'event': 'prompt', 'stage': 'selected', 'choice': 'granted', 'trigger': 'settings'},
                    {'event': 'prompt', 'stage': 'os_result', 'choice': 'enable', 'trigger': 'settings'},
                    {'event': 'prompt', 'stage': 'shown', 'trigger': 'private'}]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(self.rows().exists())

    def test_user_bound_matching_and_server_kind(self):
        self.send(user=self.create_user(username='other-notification-user'))
        self.assertEqual(self.post(self.opened(kind='admin_manual')).data, {'status': 'ok'})
        self.assertFalse(self.rows().latest('pk').data['matched'])
        self.rows().delete()
        self.send()
        self.post(self.opened(kind='admin_manual'))
        data = self.rows().latest('pk').data
        self.assertTrue(data['matched'])
        self.assertEqual(data['kind'], 'daily_fast')
        self.post({'event': 'destination', 'notif_id': 'Abcd1234_efgh-56',
                   'destination': 'fast', 'result': 'unresolved'})
        self.assertTrue(self.rows().latest('pk').data['matched'])

    def test_old_send_does_not_match(self):
        send = self.send()
        Event.objects.filter(pk=send.pk).update(timestamp=timezone.now() - timedelta(days=15))
        self.post(self.opened())
        self.assertFalse(self.rows().get().data['matched'])

    def test_optional_metadata_and_invalid_headers(self):
        self.post(self.opened(), HTTP_X_APP_VERSION='private text', HTTP_X_PLATFORM='Private text')
        data = self.rows().get().data
        for key in ('session_id', 'app_version', 'platform'):
            self.assertIsNone(data[key])

    @override_settings(NOTIFICATION_TELEMETRY_ENABLED=False)
    def test_flag_off(self):
        response = self.post(self.opened())
        self.assertEqual((response.status_code, response.data), (200, {'status': 'skipped'}))
        self.assertFalse(self.rows().exists())

    def test_missing_and_inactive_types(self):
        for code, payload in (
            (EventType.NOTIFICATION_PROMPT, dict(event='prompt', stage='shown', trigger='settings')),
            (EventType.NOTIFICATION_OPENED, self.opened()),
            (EventType.NOTIFICATION_DESTINATION, dict(event='destination', notif_id='Abcd1234_efgh-56',
                                                    destination='none', result='rejected')),
        ):
            for missing in (False, True):
                with self.subTest(code=code, missing=missing):
                    if missing:
                        EventType.objects.filter(code=code).delete()
                    else:
                        EventType.objects.filter(code=code).update(is_active=False)
                    response = self.post(payload)
                    self.assertEqual((response.status_code, response.data), (200, {'status': 'skipped'}))
        self.assertFalse(self.rows().exists())

    def test_telemetry_failures_are_skipped(self):
        for boundary in ('events.services.notification_telemetry.Event.create_event',
                         'events.services.notification_telemetry.Event.objects.filter',
                         'events.views.session_cache_key'):
            with self.subTest(boundary=boundary), patch(boundary, side_effect=RuntimeError('private')), \
                    self.assertLogs('events.views', level='ERROR') as logs:
                response = self.post(self.opened())
                self.assertEqual((response.status_code, response.data), (200, {'status': 'skipped'}))
                self.assertEqual(logs.records[0].getMessage(), 'Notification telemetry write failed')
        self.assertFalse(self.rows().exists())

    def test_authentication_required(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.post(self.opened()).status_code, (401, 403))
        self.assertFalse(self.rows().exists())

    @override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {'notification_telemetry': '2/min'}})
    def test_scoped_throttle(self):
        for _ in range(2):
            self.assertEqual(self.post(self.opened()).status_code, 200)
        self.assertEqual(self.post(self.opened()).status_code, 429)
        self.assertEqual(self.rows().count(), 1)

    def test_post_insert_failure_rolls_back(self):
        def fail_after_insert(sender, instance, **kwargs):
            if instance.event_type.code == EventType.NOTIFICATION_OPENED:
                raise RuntimeError('side effect failed')

        post_save.connect(fail_after_insert, sender=Event)
        try:
            with self.assertLogs('events.views', level='ERROR'):
                response = self.post(self.opened())
        finally:
            post_save.disconnect(fail_after_insert, sender=Event)
        self.assertEqual(response.data, {'status': 'skipped'})
        self.assertFalse(self.rows().exists())

    def test_create_value_error_skips_without_logging(self):
        with patch('events.services.notification_telemetry.Event.create_event', side_effect=ValueError), \
                patch('events.views.logger.exception') as log:
            self.assertEqual(self.post(self.opened()).data, {'status': 'skipped'})
        log.assert_not_called()
        self.assertFalse(self.rows().exists())

    def test_inactive_skips_before_matching_and_envelope(self):
        EventType.objects.filter(code=EventType.NOTIFICATION_OPENED).update(is_active=False)
        with patch('events.services.notification_telemetry.Event.objects.filter') as matching, \
                patch('events.views._client_ui_envelope') as envelope:
            self.assertEqual(self.post(self.opened()).data, {'status': 'skipped'})
        matching.assert_not_called()
        envelope.assert_not_called()

    def test_json_only(self):
        for content_type in ('application/x-www-form-urlencoded', 'multipart/form-data; boundary=test'):
            response = self.client.generic('POST', self.url, b'event=opened', content_type=content_type)
            self.assertEqual(response.status_code, 415)
        self.assertFalse(self.rows().exists())

    def test_dedupe_user_type_and_window(self):
        other = self.create_user(username='duplicate-other')
        Event.create_event(EventType.NOTIFICATION_OPENED, user=other,
                           data={'notif_id': 'Abcd1234_efgh-56'})
        self.assertEqual(self.post(self.opened()).data, {'status': 'ok'})
        self.assertEqual(self.post(self.opened()).data, {'status': 'duplicate'})
        destination = dict(event='destination', notif_id='Abcd1234_efgh-56', destination='fast', result='opened')
        self.assertEqual(self.post(destination).data, {'status': 'ok'})
        self.assertEqual(self.post(destination).data, {'status': 'duplicate'})
        self.assertEqual(self.rows().filter(user=self.user).count(), 2)
        Event.objects.filter(user=self.user).update(timestamp=timezone.now() - timedelta(days=15))
        self.assertEqual(self.post(self.opened()).data, {'status': 'ok'})

    def test_prompt_daily_cap_and_dimensions(self):
        payload = dict(event='prompt', stage='shown', trigger='settings')
        now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        with patch('events.services.notification_telemetry.timezone.now', return_value=now):
            for _ in range(10):
                self.assertEqual(self.post(payload).data, {'status': 'ok'})
            self.assertEqual(self.post(payload).data, {'status': 'capped'})
            self.assertEqual(self.post(payload | {'trigger': 'other'}).data, {'status': 'ok'})
            self.assertEqual(self.post(payload | {'stage': 'selected', 'choice': 'enable'}).data, {'status': 'ok'})
            other = self.create_user(username='cap-other')
            self.client.force_authenticate(user=other)
            self.assertEqual(self.post(payload).data, {'status': 'ok'})
            self.client.force_authenticate(user=self.user)
        with patch('events.services.notification_telemetry.timezone.now', return_value=now + timedelta(days=1)):
            self.assertEqual(self.post(payload).data, {'status': 'ok'})
        self.assertEqual(self.rows().count(), 14)

    def test_prompt_cap_cache_failure_fails_open(self):
        payload = dict(event='prompt', stage='shown', trigger='settings')
        with patch('events.services.notification_telemetry.cache.add', side_effect=RuntimeError):
            self.assertEqual(self.post(payload).data, {'status': 'ok'})
        self.assertEqual(self.rows().count(), 1)

    def test_prompt_increment_failure_fails_open(self):
        payload = dict(event='prompt', stage='shown', trigger='settings')
        self.assertEqual(self.post(payload).data, {'status': 'ok'})
        with patch('events.services.notification_telemetry.cache.incr', side_effect=RuntimeError):
            self.assertEqual(self.post(payload).data, {'status': 'ok'})
        self.assertEqual(self.rows().count(), 2)

    @override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {}})
    def test_default_notification_throttle(self):
        for _ in range(30):
            self.assertEqual(self.post(self.opened()).status_code, 200)
        self.assertEqual(self.post(self.opened()).status_code, 429)
        self.assertEqual(self.rows().count(), 1)

    def test_invalid_occurred_at_returns_400(self):
        now = timezone.now()
        for value in (None, 'not-a-date', now.replace(tzinfo=None).isoformat(),
                      (now - timedelta(hours=25)).isoformat(),
                      (now + timedelta(minutes=6)).isoformat()):
            with self.subTest(value=value):
                self.assertEqual(self.post(self.opened(occurred_at=value)).status_code, 400)
        self.assertFalse(self.rows().exists())

    def test_shared_header_vocabulary(self):
        self.post(self.opened(), HTTP_X_APP_VERSION='1.2-beta+4', HTTP_X_PLATFORM='desktop')
        self.assertEqual(self.rows().get().data['platform'], 'desktop')
        self.assertEqual(self.rows().get().data['app_version'], '1.2-beta+4')

    def test_occurred_at_normalization_and_session(self):
        now = timezone.now().replace(microsecond=0)
        cache.set(session_cache_key(self.user.pk), {'id': 'current-session'})
        for age, expected in ((None, 'current-session'), (10, 'current-session'),
                              (31, None), (120, None), (-4, 'current-session')):
            with self.subTest(age=age):
                self.rows().delete()
                payload = self.opened()
                if age is not None:
                    payload['occurred_at'] = (now - timedelta(minutes=age)).isoformat()
                with patch('events.event_schema.timezone.now', return_value=now):
                    self.assertEqual(self.post(payload).data, {'status': 'ok'})
                data = self.rows().get().data
                self.assertEqual(data['session_id'], expected)
                if age is None:
                    self.assertNotIn('occurred_at', data)
                else:
                    self.assertEqual(data['occurred_at'], payload['occurred_at'])


class NotificationTelemetryValidatorTests(SimpleTestCase):
    def test_validation_allowlists_and_utc(self):
        now = timezone.now().replace(microsecond=0)
        value = now.astimezone(datetime_timezone(timedelta(hours=2))).isoformat()
        with patch('events.event_schema.timezone.now', return_value=now):
            code, data = validate_notification_telemetry({
                'event': 'prompt', 'stage': 'shown', 'trigger': 'settings',
                'occurred_at': value, 'private': 'discarded',
            })
        self.assertEqual(code, EventType.NOTIFICATION_PROMPT)
        self.assertEqual(data, {'source': 'client_ui', 'stage': 'shown', 'trigger': 'settings',
                                'occurred_at': now.isoformat()})

    def test_invalid_vocabulary_without_database(self):
        valid = dict(event='opened', notif_id='Abcd1234_efgh-56', entry='tap', app_state='cold')
        for body in ([], {}, valid | {'entry': []}, valid | {'notif_id': 'bad'},
                     valid | {'entry': 'cold_start', 'app_state': 'warm'},
                     dict(event='destination', notif_id='Abcd1234_efgh-56', destination='none', result='opened'),
                     dict(event='prompt', stage='selected', choice='granted', trigger='settings')):
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_notification_telemetry(body)
        self.assertEqual(validate_notification_telemetry(valid)[0], EventType.NOTIFICATION_OPENED)

    def test_strict_time_validation_and_bounds(self):
        now = timezone.now().replace(microsecond=0)
        payload = dict(event='prompt', stage='shown', trigger='settings')
        invalid = [None, 123, [], '2026-10-20', now.replace(tzinfo=None).isoformat(),
                   '2026-02-30T12:00:00Z', '2026-10-20T12:00:00+99:00',
                   '2026-10-20T12:00:00+01:99',
                   (now - timedelta(hours=24, seconds=1)).isoformat(),
                   (now + timedelta(minutes=5, seconds=1)).isoformat()]
        with patch('events.event_schema.timezone.now', return_value=now):
            for value in invalid:
                with self.subTest(value=value), self.assertRaisesMessage(ValueError, 'Invalid occurred_at'):
                    validate_notification_telemetry(payload | {'occurred_at': value})
            for value in (now - timedelta(hours=24), now + timedelta(minutes=5)):
                _, data = validate_notification_telemetry(payload | {'occurred_at': value.isoformat()})
                self.assertEqual(data['occurred_at'], value.isoformat())
