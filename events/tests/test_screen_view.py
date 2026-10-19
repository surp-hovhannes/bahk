"""Contract and privacy checks for explicit UI telemetry."""
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from events.middleware import session_cache_key
from events.models import Event, EventType, UserActivityFeed
from tests.base import BaseAPITestCase


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ScreenViewTests(BaseAPITestCase):
    def setUp(self):
        cache.clear()
        self.user = self.authenticate(self.create_user(username='screen-user'))
        self.url = reverse('events:track-screen-view')

    def tearDown(self):
        cache.clear()

    def screen_views(self):
        return Event.objects.filter(event_type__code=EventType.SCREEN_VIEW)

    def test_valid_payload_and_privacy(self):
        cache.set(session_cache_key(self.user.id), {'id': 'cached-session'})
        response = self.client.post(self.url + '?secret=private', {
            'screen': 'fasts_list', 'previous_screen': 'home',
            'path': '/private/123', 'schema_version': 1, 'source': 'api',
        }, format='json', HTTP_X_APP_VERSION='1.2', HTTP_X_PLATFORM='ios',
            REMOTE_ADDR='192.0.2.1', HTTP_USER_AGENT='private-agent',
            HTTP_X_FORWARDED_FOR='192.0.2.2')
        self.assertEqual(response.status_code, 200)
        event = self.screen_views().get()
        self.assertEqual(event.data, {
            'schema_version': 2, 'source': 'client_ui', 'screen': 'fasts_list',
            'previous_screen': 'home', 'session_id': 'cached-session',
            'app_version': '1.2', 'platform': 'ios',
        })
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.user_agent, '')

    def test_minimal_payload(self):
        self.assertEqual(self.client.post(self.url, {'screen': 'a' * 64}, format='json').status_code, 200)
        event = self.screen_views().get()
        self.assertIsNone(event.data['session_id'])
        self.assertIsNone(event.data['app_version'])
        self.assertIsNone(event.data['platform'])
        self.assertNotIn('previous_screen', event.data)

    def test_empty_previous_screen_means_none(self):
        for value in (None, ''):
            with self.subTest(value=value):
                response = self.client.post(self.url, {'screen': 'home', 'previous_screen': value}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('previous_screen', self.screen_views().latest('pk').data)
        self.assertEqual(self.screen_views().count(), 2)

    def test_invalid_names(self):
        invalid = ('/fast/123', '123', 'a' * 65, 'Home', 'free text', 123, [], {}, 'home\n')
        for key, values in (('screen', (None, '') + invalid), ('previous_screen', invalid)):
            for value in values:
                with self.subTest(key=key, value=value):
                    payload = {'screen': 'home', key: value}
                    self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, [], format='json').status_code, 400)
        self.assertFalse(self.screen_views().exists())

    def test_unvalidated_headers_are_not_stored(self):
        for app_version, platform in (
            ('x' * 5000, 'p' * 5000),
            ('1.2 user@example.com', 'iOS 17; private'),
            ('', ''),
        ):
            with self.subTest(app_version=app_version[:20], platform=platform[:20]):
                response = self.client.post(self.url, {'screen': 'home'}, format='json',
                                            HTTP_X_APP_VERSION=app_version, HTTP_X_PLATFORM=platform)
                self.assertEqual(response.status_code, 200)
                data = self.screen_views().latest('pk').data
                self.assertIsNone(data['app_version'])
                self.assertIsNone(data['platform'])
        self.client.post(self.url, {'screen': 'home'}, format='json',
                         HTTP_X_APP_VERSION='1.11.0-beta.2+45', HTTP_X_PLATFORM='android')
        data = self.screen_views().latest('pk').data
        self.assertEqual((data['app_version'], data['platform']), ('1.11.0-beta.2+45', 'android'))

    def test_inactive_screen_view_type_is_skipped_not_500(self):
        EventType.objects.filter(code=EventType.SCREEN_VIEW).update(is_active=False)
        response = self.client.post(self.url, {'screen': 'home'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'status': 'skipped'})
        self.assertFalse(self.screen_views().exists())

    def test_authentication_required(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, (401, 403))
        self.assertFalse(self.screen_views().exists())

    def test_scoped_throttle(self):
        for _ in range(120):
            self.assertEqual(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, 429)
        self.assertEqual(self.screen_views().count(), 120)

    def test_throttle_rate_comes_from_settings(self):
        with override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {'screen_view': '2/min'}}):
            for _ in range(2):
                self.assertEqual(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, 200)
            self.assertEqual(self.client.post(self.url, {'screen': 'home'}, format='json').status_code, 429)

    def test_factory_stamps_without_mutating_input(self):
        for data in (None, {}, {'custom': 'value'}):
            with self.subTest(data=data):
                original = None if data is None else dict(data)
                event = Event.create_event(EventType.APP_OPEN, user=self.user, data=data)
                self.assertEqual(event.data['schema_version'], 2)
                self.assertEqual(data, original)
        event = Event.create_event(EventType.APP_OPEN, data={'schema_version': 1})
        self.assertEqual(event.data['schema_version'], 1)

    def test_feed_items_omit_schema_version(self):
        event = Event.create_event(EventType.USER_ACCOUNT_CREATED, user=self.user, data={'custom': 'value'})
        self.assertEqual(event.data['schema_version'], 2)
        feed_item = UserActivityFeed.create_from_event(event)
        self.assertEqual(feed_item.data, {'custom': 'value'})
