from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from notifications.models import DeviceToken
from notifications.tasks import send_admin_push_batch_task
from notifications.utils import send_push_notification


User = get_user_model()


class SendPushNotificationUtilsTests(TestCase):
    @patch('notifications.utils.PushClient.publish')
    def test_send_push_notification_filters_by_token_ids(self, mock_publish):
        """Ensure send_push_notification can target specific token IDs."""
        user = User.objects.create(username='user1')
        token1 = DeviceToken.objects.create(user=user, token='ExponentPushToken[token-1]', device_type='ios')
        DeviceToken.objects.create(user=user, token='ExponentPushToken[token-2]', device_type='ios', is_active=False)

        result = send_push_notification(
            message='hello',
            token_ids=[token1.id],
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['sent'], 1)
        mock_publish.assert_called_once()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class SendAdminPushTaskTests(TestCase):
    @patch('notifications.tasks.send_push_notification')
    def test_task_chunks_and_aggregates_results(self, mock_send):
        """Task should chunk IDs and aggregate send results."""
        user = User.objects.create(username='user2')
        token_a = DeviceToken.objects.create(user=user, token='ExponentPushToken[token-a]', device_type='ios')
        token_b = DeviceToken.objects.create(user=user, token='ExponentPushToken[token-b]', device_type='ios')

        mock_send.side_effect = [
            {'success': True, 'sent': 1, 'failed': 0, 'invalid_tokens': [], 'errors': []},
            {'success': True, 'sent': 1, 'failed': 0, 'invalid_tokens': [], 'errors': []},
        ]

        result = send_admin_push_batch_task(
            message='hello',
            data={'key': 'value'},
            token_ids=[token_a.id, token_b.id],
            chunk_size=1,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['sent'], 2)
        self.assertEqual(mock_send.call_count, 2)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class AdminActionEnqueueTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pass')
        self.client.force_login(self.admin)

        self.token = DeviceToken.objects.create(
            user=self.admin,
            token='ExponentPushToken[token-admin]',
            device_type='ios',
        )

    @patch('notifications.tasks.send_push_notification')
    def test_admin_action_queues_task(self, mock_send):
        session = self.client.session
        session['selected_tokens'] = [self.token.id]
        session.save()

        response = self.client.post(
            reverse('admin:notifications_devicetoken_send_push'),
            data={'message': 'hello', 'data': '{}'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.url.endswith(reverse('admin:notifications_devicetoken_changelist'))
        )
        mock_send.assert_called_once()

