"""Notification ingestion contracts; no sender or provider calls."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

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
        self.post(self.opened(), HTTP_X_APP_VERSION='private text', HTTP_X_PLATFORM='private')
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
        for boundary in ('events.views.Event.create_event', 'events.views.Event.objects.filter',
                         'events.views.session_cache_key'):
            with self.subTest(boundary=boundary), patch(boundary, side_effect=RuntimeError('private')):
                response = self.post(self.opened())
                self.assertEqual((response.status_code, response.data), (200, {'status': 'skipped'}))
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
        self.assertEqual(self.rows().count(), 2)
