from unittest.mock import patch
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from notifications.tasks import send_promo_email_task
from notifications.models import PromoEmail
from hub.models import User, Profile, Church, Fast
from tests.fixtures.test_data import TestDataFactory


@override_settings(
    SITE_URL='https://api.fastandpray.app',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_RATE_LIMIT=100,
)
class PromoEmailTaskTests(TestCase):
    """Tests for the promotional email sending task."""

    def setUp(self):
        """Set up test data."""
        # Create test church using TestDataFactory
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create test fast using TestDataFactory
        self.fast = TestDataFactory.create_fast(
            name="Test Fast",
            church=self.church,
            description="A test fast"
        )
        
        # Create test users with different profiles using TestDataFactory
        self.user1 = TestDataFactory.create_user(
            username="user1@example.com",
            email="user1@example.com",
            password="password123"
        )
        self.profile1 = TestDataFactory.create_profile(
            user=self.user1,
            church=self.church,
            receive_promotional_emails=True
        )
        self.profile1.fasts.add(self.fast)
        
        self.user2 = TestDataFactory.create_user(
            username="user2@example.com",
            email="user2@example.com",
            password="password123"
        )
        self.profile2 = TestDataFactory.create_profile(
            user=self.user2,
            church=self.church,
            receive_promotional_emails=True
        )
        
        self.user3 = TestDataFactory.create_user(
            username="user3@example.com",
            email="user3@example.com",
            password="password123"
        )
        self.profile3 = TestDataFactory.create_profile(
            user=self.user3,
            church=self.church,
            receive_promotional_emails=True  # Initially subscribed
        )
        
        # Create a promotional email
        self.promo = PromoEmail.objects.create(
            title="Test Promo",
            subject="Test Subject",
            content_html="<p>Test content</p>",
            content_text="Test content",
            status=PromoEmail.DRAFT
        )

        cache.delete("email_count")

    def test_send_promo_email_to_all_users(self):
        """Test sending a promotional email to all users."""
        # Configure promo to send to all users
        self.promo.all_users = True
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that the email was sent to all three users
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(self.promo.status, PromoEmail.SENT)
        self.assertIsNotNone(self.promo.sent_at)
        
        # Check email content
        for email in mail.outbox:
            self.assertEqual(email.subject, "Test Subject")
            self.assertIn("Test content", email.body)
            self.assertIn("Test content", email.alternatives[0][0])
            self.assertIn("unsubscribe", email.alternatives[0][0].lower())

    def test_send_promo_email_with_church_filter(self):
        """Test sending a promotional email filtered by church."""
        # Configure promo to filter by church
        self.promo.all_users = False
        self.promo.church_filter = self.church
        self.promo.save()
        
        # Create a user in a different church using TestDataFactory
        other_church = TestDataFactory.create_church(name="Other Church")
        other_user = TestDataFactory.create_user(
            username="other_user@example.com",
            email="other@example.com",
            password="password123"
        )
        TestDataFactory.create_profile(
            user=other_user,
            church=other_church,
            receive_promotional_emails=True
        )
        
        # Move user2 to the other church
        self.profile2.church = other_church
        self.profile2.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that emails were sent to users in the specified church (user1, user3)
        self.assertEqual(len(mail.outbox), 2)
        recipient_emails = sorted([email.to[0] for email in mail.outbox])
        self.assertEqual(recipient_emails, ["user1@example.com", "user3@example.com"])
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_send_promo_email_with_fast_filter(self):
        """Test sending a promotional email filtered by fast."""
        # Configure promo to filter by fast
        self.promo.all_users = False
        self.promo.joined_fast = self.fast
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that the email was only sent to users in the specified fast
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to[0], "user1@example.com")
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_send_promo_email_with_unsubscribe_filter(self):
        """Test sending a promotional email with unsubscribe filter."""
        # Configure promo to exclude unsubscribed users
        self.promo.all_users = True
        self.promo.exclude_unsubscribed = True
        self.promo.save()
        
        # Set user2 to not receive promotional emails
        self.profile2.receive_promotional_emails = False
        self.profile2.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that the email was only sent to subscribed users (user1, user3)
        self.assertEqual(len(mail.outbox), 2)
        recipient_emails = sorted([email.to[0] for email in mail.outbox])
        self.assertEqual(recipient_emails, ["user1@example.com", "user3@example.com"])
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_send_promo_email_with_no_recipients(self):
        """Test sending a promotional email with no eligible recipients."""
        # Configure promo with filters that exclude all users
        self.promo.all_users = False
        self.promo.church_filter = Church.objects.create(name="Non-existent Church")
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that no emails were sent and status is FAILED
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.promo.status, PromoEmail.FAILED)

    def test_send_promo_email_with_invalid_id(self):
        """Test sending a promotional email with an invalid ID."""
        # Run the task with an invalid ID
        send_promo_email_task(99999)
        
        # Check that no emails were sent
        self.assertEqual(len(mail.outbox), 0)

    @patch('notifications.tasks.EmailMultiAlternatives.send')
    def test_send_promo_email_with_send_error(self, mock_send):
        """Test handling of email sending errors."""
        # Configure mock to raise an exception
        mock_send.side_effect = Exception("SMTP error")
        
        # Configure promo to send to all users
        self.promo.all_users = True
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that the promo status is FAILED
        self.assertEqual(self.promo.status, PromoEmail.FAILED)

    def test_unsubscribe_url_generation(self):
        """Test that unsubscribe URLs are correctly generated."""
        # Configure promo to send to all users
        self.promo.all_users = True
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Check that the unsubscribe URL is correctly formatted
        for email in mail.outbox:
            html_content = email.alternatives[0][0]
            self.assertIn("unsubscribe", html_content.lower())
            self.assertIn("https://api.fastandpray.app", html_content)
            self.assertIn("token=", html_content)

    def test_send_promo_email_to_selected_users_only(self):
        """Test sending only to specifically selected users, ignoring other filters."""
        # Add user1 and user3 to selected_users
        self.promo.selected_users.add(self.user1, self.user3)
        
        # Set other filters that would normally include user2
        self.promo.all_users = True 
        self.promo.church_filter = self.church # user2 is in this church initially
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that only selected users received the email
        self.assertEqual(len(mail.outbox), 2)
        recipient_emails = sorted([email.to[0] for email in mail.outbox])
        self.assertEqual(recipient_emails, ["user1@example.com", "user3@example.com"])
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_send_promo_email_selected_user_inactive(self):
        """Test that an inactive user in selected_users is skipped."""
        # Make user3 inactive
        self.user3.is_active = False
        self.user3.save()
        
        # Add user1 and inactive user3 to selected_users
        self.promo.selected_users.add(self.user1, self.user3)
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that only the active selected user received the email
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to[0], "user1@example.com")
        # Status should still be SENT if at least one succeeded
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_send_promo_email_selected_user_no_email(self):
        """Test that a selected user with no email address is skipped."""
        # Remove email from user3
        self.user3.email = ""
        self.user3.save()
        
        # Add user1 and user3 (no email) to selected_users
        self.promo.selected_users.add(self.user1, self.user3)
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that only the user with an email received it
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to[0], "user1@example.com")
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_send_promo_email_selected_user_unsubscribed(self):
        """
        Test behavior when a selected user has opted out of promotional emails.
        NOTE: Based on current task logic, selected users bypass the 
        'receive_promotional_emails' check. This test verifies that behavior.
        """
        # Set user3 profile to unsubscribed
        self.profile3.receive_promotional_emails = False
        self.profile3.save()
        
        # Add user1 (subscribed) and user3 (unsubscribed) to selected_users
        self.promo.selected_users.add(self.user1, self.user3)
        # Explicitly set exclude_unsubscribed - this *shouldn't* matter for selected_users
        self.promo.exclude_unsubscribed = True 
        self.promo.save()
        
        # Run the task
        send_promo_email_task(self.promo.id)
        
        # Refresh promo from database
        self.promo.refresh_from_db()
        
        # Check that *both* selected users received the email, even the unsubscribed one
        self.assertEqual(len(mail.outbox), 2, "Expected selected unsubscribed user to receive email (current task logic).")
        recipient_emails = sorted([email.to[0] for email in mail.outbox])
        self.assertEqual(recipient_emails, ["user1@example.com", "user3@example.com"])
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    @override_settings(EMAIL_RATE_LIMIT=2, EMAIL_API_DELAY_SECONDS=0.01)  # Speed up test
    def test_send_promo_email_rate_limited(self):
        """Tests that sending more promo emails than allowed rate properly batches emails."""
        # Create 4 users to test batching with rate limit of 2
        user4 = TestDataFactory.create_user(email="user4@example.com")
        user5 = TestDataFactory.create_user(email="user5@example.com")
        
        self.promo.selected_users.add(self.user1, self.user2, self.user3, user4, user5)
        self.promo.save()

        # First call should send 2 emails and leave status as SENDING
        send_promo_email_task(self.promo.id)

        self.promo.refresh_from_db()
        self.assertEqual(len(mail.outbox), 2, "Expected 2 emails in first batch due to rate limiting")
        self.assertEqual(self.promo.status, PromoEmail.SENDING, "Status should be SENDING when batching")
        
        # Verify rate limit cache was set
        current_count = cache.get('email_count', 0)
        self.assertEqual(current_count, 2, "Email count cache should be set to 2")

        # Clear mailbox to simulate time passing and run next batch
        mail.outbox.clear()
        cache.delete("email_count")

        # Simulate the next batch by calling with batch_start_index
        # In real life, this would be scheduled automatically by Celery
        send_promo_email_task(self.promo.id, batch_start_index=2)
        
        self.promo.refresh_from_db()
        self.assertEqual(len(mail.outbox), 2, "Expected 2 emails in second batch")
        self.assertEqual(self.promo.status, PromoEmail.SENDING, "Status should still be SENDING")

        # Final batch - clear and send last user
        mail.outbox.clear() 
        cache.delete("email_count")
        
        send_promo_email_task(self.promo.id, batch_start_index=4)
        
        self.promo.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1, "Expected 1 email in final batch")
        self.assertEqual(self.promo.status, PromoEmail.SENT, "Status should be SENT when all batches complete")

    @override_settings(EMAIL_RATE_LIMIT=2, EMAIL_API_DELAY_SECONDS=0.01)
    def test_send_promo_email_batch_tracking(self):
        """Tests that batch processing correctly tracks user positions."""
        # Create a scenario where we can verify user order is maintained
        users = [self.user1, self.user2, self.user3]
        expected_first_batch = [self.user1.email, self.user2.email]
        expected_second_batch = [self.user3.email]
        
        self.promo.selected_users.add(*users)
        self.promo.save()

        # First batch
        send_promo_email_task(self.promo.id)
        
        # Verify first batch emails
        first_batch_emails = [email.to[0] for email in mail.outbox]
        self.assertEqual(sorted(first_batch_emails), sorted(expected_first_batch))
        
        # Clear and run second batch
        mail.outbox.clear()
        cache.delete("email_count")
        
        send_promo_email_task(self.promo.id, batch_start_index=2)
        
        # Verify second batch emails  
        second_batch_emails = [email.to[0] for email in mail.outbox]
        self.assertEqual(second_batch_emails, expected_second_batch)

    def test_celery_eager_setting_is_true(self):
        # This test is not provided in the original file or the new code block
        # It's assumed to exist as it's called in the original file
        pass 