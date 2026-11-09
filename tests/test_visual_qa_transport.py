"""Exercise the real admin warning offline, including fixture isolation."""
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings

from notifications.admin import DeviceTokenAdmin
from notifications.models import DeviceToken
from tests.visual_qa_transport import TOKEN, USERNAME, TransportWarningFixtureMiddleware, seed


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
    SESSION_ENGINE='django.contrib.sessions.backends.db',
)
class TransportVisualFixtureTests(TestCase):
    def test_production_and_ordinary_test_settings_do_not_install_fixture(self):
        from bahk import settings as production
        from tests import visual_qa_settings

        self.assertNotIn('tests.visual_qa_transport.TransportWarningFixtureMiddleware', production.MIDDLEWARE)
        self.assertFalse(visual_qa_settings.SEND_PUSH_NOTIFICATIONS)
        with self.assertRaises(ImproperlyConfigured):
            TransportWarningFixtureMiddleware(lambda request: None)
        with self.assertRaises(ImproperlyConfigured):
            seed()

    @override_settings(SETTINGS_MODULE='tests.visual_qa_settings')
    def test_actual_action_renders_warning_without_network_sleep_or_leakage(self):
        get_user_model().objects.create(username=USERNAME, email='fixture@example.test')
        seed()
        seed()
        device = DeviceToken.objects.get(token=TOKEN)
        admin = get_user_model().objects.create_superuser(
            username='fixture-admin', email='admin@example.test', password='test',
        )
        self.client.force_login(admin)
        middleware = [*settings.MIDDLEWARE, 'tests.visual_qa_transport.TransportWarningFixtureMiddleware']
        real_report = DeviceTokenAdmin.report_transport_status
        with (
            override_settings(MIDDLEWARE=middleware, SETTINGS_MODULE='tests.visual_qa_settings'),
            patch('requests.sessions.Session.request', side_effect=AssertionError('network')) as network,
            patch('notifications.utils.time.sleep', side_effect=AssertionError('sleep')) as sleep,
            patch('notifications.receipts.remember_tickets') as receipts,
            patch('notifications.admin.DeviceTokenAdmin.report_transport_status', autospec=True) as report,
        ):
            report.side_effect = real_report
            response = self.client.post('/admin/notifications/devicetoken/', {
                'action': 'send_test_notification', '_selected_action': str(device.pk), 'index': '0',
            }, follow=True)
            self.assertContains(response, 'Skipped: 0. Retry circuit open: True.')
            result = report.call_args.args[2]
            self.assertEqual((result['sent'], result['failed'], result['skipped']), (0, 1, 0))
            self.assertEqual(len(result['transport_errors']), 3)
            self.assertNotContains(response, 'Synthetic offline rejection')
            network.assert_not_called()
            sleep.assert_not_called()
            self.assertFalse(settings.SEND_PUSH_NOTIFICATIONS)
            receipts.assert_called_once_with([])
            # An ordinary action is still disabled after the fixture patches exit.
            other = DeviceToken.objects.create(user=admin, token='ExponentPushToken[other]', device_type='ios')
            response = self.client.post('/admin/notifications/devicetoken/', {
                'action': 'send_test_notification', '_selected_action': str(other.pk), 'index': '0',
            }, follow=True)
            self.assertNotContains(response, 'Retry circuit open: True')
            network.assert_not_called()
            sleep.assert_not_called()

    @override_settings(SETTINGS_MODULE='tests.visual_qa_settings')
    def test_exception_restores_provider_and_settings(self):
        from notifications import utils

        user = get_user_model().objects.create(username=USERNAME, is_superuser=True)
        seed()
        original_provider, original_sleep = utils.PushClient, utils.time.sleep
        request = RequestFactory().post('/admin/notifications/devicetoken/', {
            'action': 'send_test_notification',
            '_selected_action': str(DeviceToken.objects.get(token=TOKEN).pk),
        })
        request.user = user

        def failing_action(request):
            self.assertFalse(settings.SEND_PUSH_NOTIFICATIONS)
            self.assertIsNot(utils.PushClient, original_provider)
            raise RuntimeError('fixture action failed')

        with self.assertRaisesRegex(RuntimeError, 'fixture action failed'):
            TransportWarningFixtureMiddleware(failing_action)(request)
        self.assertFalse(settings.SEND_PUSH_NOTIFICATIONS)
        self.assertIs(utils.PushClient, original_provider)
        self.assertIs(utils.time.sleep, original_sleep)
