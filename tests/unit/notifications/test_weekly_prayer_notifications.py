"""Unit tests for weekly prayer request notifications."""
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone

from notifications.tasks import send_weekly_prayer_request_push_notification_task
from prayers.models import PrayerRequest, PrayerRequestAcceptance
from tests.base import BaseTestCase


class WeeklyPrayerRequestNotificationTests(BaseTestCase):
    """Test weekly prayer request notification task."""

    def setUp(self):
        super().setUp()
        self.user1 = self.create_user(email='user1@example.com')
        self.user2 = self.create_user(email='user2@example.com')
        self.requester = self.create_user(email='requester@example.com')

        # Create profiles with preference enabled
        self.user1.profile.receive_weekly_prayer_request_push_notifications = True
        self.user1.profile.save()
        self.user2.profile.receive_weekly_prayer_request_push_notifications = True
        self.user2.profile.save()

    def create_prayer_request(self, requester, **overrides):
        """Helper to create an approved prayer request."""
        defaults = {
            'title': 'Test Prayer Request',
            'description': 'Please pray for this need.',
            'requester': requester,
            'duration_days': 3,
            'status': 'approved',
            'reviewed': True,
        }
        defaults.update(overrides)
        return PrayerRequest.objects.create(**defaults)

    @patch('notifications.tasks.send_push_notification_task')
    def test_sends_notification_when_requests_exist(self, mock_send):
        """Test that notification is sent when active requests exist."""
        # Create an active prayer request
        prayer_request = self.create_prayer_request(self.requester)

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify notification was sent
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        message, data, users, notification_type = args

        self.assertIn('prayer requests', message.lower())
        self.assertEqual(data['screen'], 'prayer-requests')
        self.assertEqual(notification_type, 'weekly_prayer_requests')
        self.assertGreater(len(users), 0)

    @patch('notifications.tasks.send_push_notification_task')
    def test_no_notification_when_no_requests(self, mock_send):
        """Test that no notification is sent when no active requests exist."""
        # Don't create any prayer requests

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify no notification was sent
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_expired_requests(self, mock_send):
        """Test that expired requests are not counted."""
        # Create an expired request
        expired_request = self.create_prayer_request(self.requester)
        expired_request.expiration_date = timezone.now() - timedelta(days=1)
        expired_request.save()

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify no notification was sent (no active requests)
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_users_who_accepted_all_requests(self, mock_send):
        """Test that users who accepted all requests are not notified."""
        # Create a prayer request
        prayer_request = self.create_prayer_request(self.requester)

        # User1 accepts it
        PrayerRequestAcceptance.objects.create(
            prayer_request=prayer_request,
            user=self.user1
        )

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify notification was sent, but user1 is not in the list
        if mock_send.called:
            args, kwargs = mock_send.call_args
            message, data, users, notification_type = args
            self.assertNotIn(self.user1, users)

    @patch('notifications.tasks.send_push_notification_task')
    def test_includes_users_with_unaccepted_requests(self, mock_send):
        """Test that users with unaccepted requests are notified."""
        # Create a prayer request
        prayer_request = self.create_prayer_request(self.requester)

        # User1 has not accepted it

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify user1 is in the notification list
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        message, data, users, notification_type = args
        self.assertIn(self.user1, users)

    @patch('notifications.tasks.send_push_notification_task')
    def test_message_format_includes_count(self, mock_send):
        """Test that message includes the count of prayer requests."""
        # Create multiple prayer requests
        self.create_prayer_request(self.requester, title='Request 1')
        self.create_prayer_request(self.requester, title='Request 2')
        self.create_prayer_request(self.requester, title='Request 3')

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify message contains a number
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        message, data, users, notification_type = args

        # Check message format
        self.assertIn('prayer requests', message.lower())
        # Verify it contains "3" (the count)
        self.assertIn('3', message)

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_pending_moderation_requests(self, mock_send):
        """Test that pending requests are not counted."""
        # Create a pending request
        self.create_prayer_request(
            self.requester,
            status='pending_moderation'
        )

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify no notification (no approved requests)
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_inactive_users(self, mock_send):
        """Test that inactive users are not notified."""
        # Create a prayer request
        prayer_request = self.create_prayer_request(self.requester)

        # Deactivate user1
        self.user1.is_active = False
        self.user1.save()

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify user1 is not in notification list
        if mock_send.called:
            args, kwargs = mock_send.call_args
            message, data, users, notification_type = args
            self.assertNotIn(self.user1, users)

    @patch('notifications.tasks.send_push_notification_task')
    def test_handles_anonymous_requests(self, mock_send):
        """Test that anonymous requests are included in count."""
        # Create an anonymous request
        self.create_prayer_request(
            self.requester,
            is_anonymous=True
        )

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify notification was sent
        mock_send.assert_called_once()

    @patch('notifications.tasks.send_push_notification_task')
    def test_deep_link_payload_format(self, mock_send):
        """Test that deep link payload is correctly formatted."""
        # Create a prayer request
        self.create_prayer_request(self.requester)

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify payload format
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        message, data, users, notification_type = args

        self.assertEqual(data, {"screen": "prayer-requests"})

    @patch('notifications.tasks.send_push_notification_task')
    def test_counts_multiple_unaccepted_requests(self, mock_send):
        """Test counting when user has multiple unaccepted requests."""
        # Create 3 requests, user accepts 1
        req1 = self.create_prayer_request(self.requester, title='Request 1')
        req2 = self.create_prayer_request(self.requester, title='Request 2')
        req3 = self.create_prayer_request(self.requester, title='Request 3')

        PrayerRequestAcceptance.objects.create(
            prayer_request=req1,
            user=self.user1
        )

        # User1 should have 2 unaccepted requests
        unaccepted = PrayerRequest.objects.get_active_approved().exclude(
            acceptances__user=self.user1
        ).count()
        self.assertEqual(unaccepted, 2)

    @patch('notifications.tasks.send_push_notification_task')
    def test_handles_expiration_timezone_correctly(self, mock_send):
        """Test that expiration checks respect timezones."""
        # Create request expiring soon
        prayer_request = self.create_prayer_request(self.requester)

        # Set expiration to 1 hour from now
        prayer_request.expiration_date = timezone.now() + timedelta(hours=1)
        prayer_request.save()

        # Should still be active
        active_requests = PrayerRequest.objects.get_active_approved()
        self.assertIn(prayer_request, active_requests)

        # Run task - should send notification
        send_weekly_prayer_request_push_notification_task()
        mock_send.assert_called_once()

        # Set expiration to 1 hour ago
        prayer_request.expiration_date = timezone.now() - timedelta(hours=1)
        prayer_request.save()

        # Should not be active
        active_requests = PrayerRequest.objects.get_active_approved()
        self.assertNotIn(prayer_request, active_requests)

    def test_message_generation_with_various_counts(self):
        """Test message generation with different request counts."""
        from notifications.constants import WEEKLY_PRAYER_REQUEST_MESSAGE

        # Test with 1 request
        message_1 = WEEKLY_PRAYER_REQUEST_MESSAGE.format(count=1)
        self.assertIn('1', message_1)
        self.assertIn('prayer requests', message_1.lower())

        # Test with 10 requests
        message_10 = WEEKLY_PRAYER_REQUEST_MESSAGE.format(count=10)
        self.assertIn('10', message_10)

        # Test with 100 requests
        message_100 = WEEKLY_PRAYER_REQUEST_MESSAGE.format(count=100)
        self.assertIn('100', message_100)

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_rejected_requests(self, mock_send):
        """Test that rejected requests are not counted."""
        # Create a rejected request
        self.create_prayer_request(
            self.requester,
            status='rejected'
        )

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify no notification (no approved requests)
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_completed_requests(self, mock_send):
        """Test that completed requests are not counted."""
        # Create a completed request
        self.create_prayer_request(
            self.requester,
            status='completed'
        )

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify no notification (no active requests)
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task')
    def test_excludes_deleted_requests(self, mock_send):
        """Test that deleted requests are not counted."""
        # Create a deleted request
        self.create_prayer_request(
            self.requester,
            status='deleted'
        )

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify no notification (no active requests)
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task')
    def test_notification_type_is_correct(self, mock_send):
        """Test that correct notification type is passed for preference filtering."""
        # Create a prayer request
        self.create_prayer_request(self.requester)

        # Run task
        send_weekly_prayer_request_push_notification_task()

        # Verify notification type
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        message, data, users, notification_type = args

        self.assertEqual(notification_type, 'weekly_prayer_requests')
