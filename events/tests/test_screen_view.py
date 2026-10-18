"""Contract and privacy checks for explicit UI telemetry."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from events.models import Event, EventType


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ScreenViewTests(TestCase):
    def setUp(self):
        cache.clear()
        EventType.get_or_create_default_types()
        self.user = get_user_model().objects.create_user(username='screen-user')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse('events:track-screen-view')

    def tearDown(self):
        cache.clear()

    def test_valid_payload_and_privacy(self):
        cache.set(f'analytics:session:{self.user.id}', {'id': 'cached-session'})
        response = self.client.post(self.url + '?secret=private', {
            'screen': 'fasts_list', 'previous_screen': 'home',
            'path': '/private/123', 'schema_version': 1, 'source': 'api',
        }, format='json', HTTP_X_APP_VERSION='1.2', HTTP_X_PLATFORM='ios',
            REMOTE_ADDR='192.0.2.1', HTTP_USER_AGENT='private-agent',
            HTTP_X_FORWARDED_FOR='192.0.2.2')
        self.assertEqual(response.status_code, 200)
        event = Event.objects.get(event_type__code=EventType.SCREEN_VIEW)
        self.assertEqual(event.data, {
            'schema_version': 2, 'source': 'client_ui', 'screen': 'fasts_list',
            'previous_screen': 'home', 'session_id': 'cached-session',
            'app_version': '1.2', 'platform': 'ios',
        })
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.user_agent, '')

    def test_minimal_payload(self):
        self.assertEqual(self.client.post(self.url, {'screen': 'a' * 64}, format='json').status_code, 200)
        event = Event.objects.get(event_type__code=EventType.SCREEN_VIEW)
        self.assertIsNone(event.data['session_id'])
        self.assertIsNone(event.data['app_version'])
        self.assertIsNone(event.data['platform'])
        self.assertNotIn('previous_screen', event.data)

    def test_invalid_names(self):
        for value in (None, '', '/fast/123', '123', 'a' * 65, 'Home', 'free text', 123, [], {}, 'home\n'):
            for key in ('screen', 'previous_screen'):
                with self.subTest(key=key, value=value):
                    payload = {'screen': 'home', key: value}
                    self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, [], format='json').status_code, 400)
        self.assertFalse(Event.objects.filter(event_type__code=EventType.SCREEN_VIEW).exists())

    def test_authentication_required(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, (401, 403))
        self.assertFalse(Event.objects.filter(event_type__code=EventType.SCREEN_VIEW).exists())

    def test_scoped_throttle(self):
        for _ in range(120):
            self.assertEqual(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, 429)
        self.assertEqual(Event.objects.filter(event_type__code=EventType.SCREEN_VIEW).count(), 120)

    def test_factory_stamps_without_mutating_input(self):
        for data in (None, {}, {'custom': 'value'}):
            with self.subTest(data=data):
                original = None if data is None else dict(data)
                event = Event.create_event(EventType.APP_OPEN, user=self.user, data=data)
                self.assertEqual(event.data['schema_version'], 2)
                self.assertEqual(data, original)
        event = Event.create_event(EventType.APP_OPEN, data={'schema_version': 1})
        self.assertEqual(event.data['schema_version'], 1)
