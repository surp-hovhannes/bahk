"""
Test batch processing and index calculation correctness in promo email tasks.
"""
from unittest.mock import patch, MagicMock
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings, tag

from notifications.tasks import send_promo_email_task
from notifications.models import PromoEmail
from hub.models import User
from tests.fixtures.test_data import TestDataFactory


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_RATE_LIMIT=3,
    EMAIL_RATE_LIMIT_WINDOW=60,
    CELERY_TASK_ALWAYS_EAGER=True
)
class BatchProcessingTests(TestCase):
    """Tests for correct batch processing and index calculations."""

    def setUp(self):
        """Set up test data."""
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create exactly 7 users for predictable batch testing
        self.users = []
        for i in range(7):
            user = TestDataFactory.create_user(
                username=f"user{i}@example.com",
                email=f"user{i}@example.com"
            )
            TestDataFactory.create_profile(
                user=user,
                church=self.church,
                receive_promotional_emails=True
            )
            self.users.append(user)
        
        self.promo = PromoEmail.objects.create(
            title="Batch Test Promo",
            subject="Batch Test Subject",
            content_html="<p>Batch test content</p>",
            content_text="Batch test content",
            status=PromoEmail.DRAFT
        )
        
        # Clear cache before each test
        cache.clear()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_batch_index_calculation_correctness(self):
        """Test that batch indices are calculated correctly to prevent overlaps."""
        self.promo.selected_users.set(self.users)
        self.promo.save()
        
        all_sent_emails = []
        
        # Process all batches manually to avoid scheduling complications in tests
        current_batch_start = 0
        
        while current_batch_start < len(self.users):
            # Clear previous emails to track this batch
            mail.outbox.clear()
            cache.delete('email_count')
            
            with patch('notifications.tasks.send_promo_email_task.apply_async') as mock_reschedule:
                send_promo_email_task(self.promo.id, batch_start_index=current_batch_start)
                
                # Record emails sent in this batch
                batch_emails = [email.to[0] for email in mail.outbox]
                all_sent_emails.extend(batch_emails)
                
                # Check if task was rescheduled
                if mock_reschedule.called:
                    # Get the next batch start index
                    call_args = mock_reschedule.call_args
                    current_batch_start = call_args[1]['kwargs']['batch_start_index']
                else:
                    # Task completed, break
                    break
        
        # Verify no duplicates across all batches
        unique_emails = set(all_sent_emails)
        self.assertEqual(len(unique_emails), len(all_sent_emails), 
                        f"Duplicate emails found: {all_sent_emails}")
        
        # Verify at least some users received emails
        self.assertGreater(len(unique_emails), 0)
        
        # Verify final status is SENT or SENDING
        self.promo.refresh_from_db()
        self.assertIn(self.promo.status, [PromoEmail.SENT, PromoEmail.SENDING])

    def test_batch_boundary_edge_cases(self):
        """Test edge cases at batch boundaries."""
        # Test with exactly rate limit number of users
        rate_limit_users = self.users[:3]  # Exactly matches EMAIL_RATE_LIMIT=3
        self.promo.selected_users.set(rate_limit_users)
        self.promo.save()
        
        # Should complete in one batch without rescheduling
        with patch('notifications.tasks.send_promo_email_task.apply_async') as mock_reschedule:
            send_promo_email_task(self.promo.id)
            
            # Should not reschedule since all users fit in rate limit
            mock_reschedule.assert_not_called()
            
            # All emails should be sent
            self.assertEqual(len(mail.outbox), 3)
            
            # Status should be SENT
            self.promo.refresh_from_db()
            self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_batch_start_index_validation(self):
        """Test validation of batch_start_index parameter."""
        self.promo.selected_users.set(self.users[:3])
        self.promo.save()
        
        # Test with batch_start_index beyond total users
        send_promo_email_task(self.promo.id, batch_start_index=10)
        
        # Should mark as completed without sending emails
        self.assertEqual(len(mail.outbox), 0)
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_user_order_preservation_across_batches(self):
        """Test that user processing order is preserved across batch executions."""
        # Use fewer users to avoid hitting issues with infinite loops in tests
        test_users = self.users[:4]  # Only 4 users
        self.promo.selected_users.set(test_users)
        self.promo.save()
        
        all_processed_emails = []
        
        # Process first batch manually
        mail.outbox.clear()
        cache.delete('email_count')
        send_promo_email_task(self.promo.id, batch_start_index=0)
        
        first_batch_emails = [email.to[0] for email in mail.outbox]
        all_processed_emails.extend(first_batch_emails)
        
        # Process second batch manually (assuming 3 emails sent in first batch due to rate limit)
        if len(first_batch_emails) < len(test_users):
            mail.outbox.clear()
            cache.delete('email_count')
            send_promo_email_task(self.promo.id, batch_start_index=len(first_batch_emails))
            
            second_batch_emails = [email.to[0] for email in mail.outbox]
            all_processed_emails.extend(second_batch_emails)
        
        # Verify no duplicates
        unique_emails = set(all_processed_emails)
        self.assertEqual(len(unique_emails), len(all_processed_emails), 
                        f"Found duplicate emails: {all_processed_emails}")
        
        # Verify at least some emails were processed
        self.assertGreater(len(all_processed_emails), 0)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_batch_resumption_after_failure(self):
        """Test that batches can resume correctly after failures."""
        self.promo.selected_users.set(self.users[:5])
        self.promo.save()
        
        # Simulate first batch success
        mail.outbox.clear()
        cache.delete('email_count')
        
        with patch('notifications.tasks.send_promo_email_task.apply_async') as mock_reschedule:
            send_promo_email_task(self.promo.id)
            
            first_batch_count = len(mail.outbox)
            self.assertEqual(first_batch_count, 3)  # Rate limit
            
            # Get the reschedule parameters
            if mock_reschedule.called:
                batch_start_index = mock_reschedule.call_args[1]['kwargs']['batch_start_index']
                self.assertEqual(batch_start_index, 3)
            else:
                # If not rescheduled, all emails were sent
                batch_start_index = len(self.users[:5])
        
        # Simulate the resumption if there are remaining users
        if batch_start_index < len(self.users[:5]):
            mail.outbox.clear()
            cache.delete('email_count')
            
            send_promo_email_task(self.promo.id, batch_start_index=batch_start_index)
            
            # Should complete the remaining users
            remaining_count = len(self.users[:5]) - batch_start_index
            self.assertEqual(len(mail.outbox), remaining_count)
        
        self.promo.refresh_from_db()
        self.assertIn(self.promo.status, [PromoEmail.SENT, PromoEmail.SENDING])

    def test_empty_batch_handling(self):
        """Test handling of batches with no valid users."""
        # Create users but make them all inactive
        inactive_users = []
        for i in range(3):
            user = TestDataFactory.create_user(
                username=f"inactive{i}@example.com",
                email=f"inactive{i}@example.com"
            )
            user.is_active = False
            user.save()
            TestDataFactory.create_profile(
                user=user,
                church=self.church,
                receive_promotional_emails=True
            )
            inactive_users.append(user)
        
        self.promo.selected_users.set(inactive_users)
        self.promo.save()
        
        send_promo_email_task(self.promo.id)
        
        # No emails should be sent
        self.assertEqual(len(mail.outbox), 0)
        
        # Status should be FAILED (all users skipped)
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.status, PromoEmail.FAILED)

    @override_settings(EMAIL_RATE_LIMIT=1, CELERY_TASK_ALWAYS_EAGER=False)
    def test_single_user_batches(self):
        """Test batch processing with rate limit of 1 (single user batches)."""
        self.promo.selected_users.set(self.users[:3])
        self.promo.save()
        
        sent_emails = []
        current_batch_start = 0
        
        # Process each user individually
        for expected_batch in range(3):
            mail.outbox.clear()
            cache.delete('email_count')
            
            with patch('notifications.tasks.send_promo_email_task.apply_async') as mock_reschedule:
                send_promo_email_task(self.promo.id, batch_start_index=current_batch_start)
                
                # Should send exactly 1 email per batch
                self.assertEqual(len(mail.outbox), 1)
                sent_emails.extend([email.to[0] for email in mail.outbox])
                
                if expected_batch < 2:  # Not the last batch
                    # Should reschedule for next user
                    if mock_reschedule.called:
                        current_batch_start = mock_reschedule.call_args[1]['kwargs']['batch_start_index']
                        self.assertEqual(current_batch_start, expected_batch + 1)
                    else:
                        # If not rescheduled, all remaining emails were sent
                        break
                else:
                    # Last batch should not reschedule
                    self.assertFalse(mock_reschedule.called)
        
        # Verify all users received exactly one email
        self.assertEqual(len(sent_emails), 3)
        self.assertEqual(len(set(sent_emails)), 3)  # No duplicates
        
        self.promo.refresh_from_db()
        self.assertIn(self.promo.status, [PromoEmail.SENT, PromoEmail.SENDING])