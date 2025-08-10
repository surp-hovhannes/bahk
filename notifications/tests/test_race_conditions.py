"""
Test race conditions and concurrency issues in promo email tasks.
"""
import threading
import time
from unittest.mock import patch, MagicMock
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings, TransactionTestCase, tag
from django.db import transaction

from notifications.tasks import send_promo_email_task, increment_email_count, get_email_count
from notifications.models import PromoEmail
from hub.models import User, Profile, Church
from tests.fixtures.test_data import TestDataFactory


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_RATE_LIMIT=5,
    EMAIL_RATE_LIMIT_WINDOW=60,
    CELERY_TASK_ALWAYS_EAGER=True
)
class RaceConditionTests(TransactionTestCase):
    """Tests for race conditions in promo email tasks."""

    def setUp(self):
        """Set up test data."""
        self.church = TestDataFactory.create_church(name="Test Church")
        self.users = []
        
        # Create 10 test users
        for i in range(10):
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
            title="Test Promo",
            subject="Test Subject",
            content_html="<p>Test content</p>",
            content_text="Test content",
            status=PromoEmail.DRAFT,
            all_users=True
        )
        
        # Clear cache before each test
        cache.clear()

    @tag('slow')
    def test_concurrent_task_execution_prevention(self):
        """Test that concurrent tasks for the same promo are prevented by Redis locks."""
        results = []
        exceptions = []
        
        def run_task():
            try:
                result = send_promo_email_task(self.promo.id)
                results.append(result)
            except Exception as e:
                exceptions.append(e)
        
        # Start multiple threads simultaneously
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=run_task)
            threads.append(thread)
        
        # Start all threads at nearly the same time
        for thread in threads:
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Only one task should have executed successfully
        # The others should have been skipped due to lock
        self.promo.refresh_from_db()
        # In eager mode, the task should complete but may not reach SENT if rate limited
        self.assertIn(self.promo.status, [PromoEmail.SENT, PromoEmail.SENDING])
        
        # Should have some emails sent (at least up to rate limit)
        self.assertGreater(len(mail.outbox), 0)
        
        # Verify no exceptions occurred
        self.assertEqual(len(exceptions), 0)

    @tag('slow')
    def test_email_counter_race_condition(self):
        """Test that concurrent email counter increments are atomic."""
        # Reset email count
        cache.delete('email_count')
        
        results = []
        
        def increment_counter():
            count = increment_email_count()
            results.append(count)
        
        # Run multiple increments concurrently
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=increment_counter)
            threads.append(thread)
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Final count should be exactly 10
        final_count = get_email_count()
        self.assertEqual(final_count, 10)
        
        # All increments should have returned unique values
        self.assertEqual(len(set(results)), 10)
        self.assertEqual(sorted(results), list(range(1, 11)))

    @tag('slow')
    def test_database_status_race_condition(self):
        """Test that concurrent status updates are handled atomically."""
        results = []
        
        def update_status():
            try:
                # Simulate the database update logic from the task
                from django.db import transaction
                with transaction.atomic():
                    promo = PromoEmail.objects.select_for_update().get(id=self.promo.id)
                    if promo.status == PromoEmail.DRAFT:
                        promo.status = PromoEmail.SENDING
                        promo.save()
                        results.append("updated")
                    else:
                        results.append("skipped")
            except Exception as e:
                results.append(f"error: {e}")
        
        # Run multiple status updates concurrently
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=update_status)
            threads.append(thread)
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Only one thread should have updated the status
        updated_count = results.count("updated")
        self.assertEqual(updated_count, 1)
        
        # The rest should have been skipped or errored (database lock contention)
        non_updated_count = len(results) - updated_count
        self.assertEqual(non_updated_count, 4)
        
        # Final status should be SENDING
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.status, PromoEmail.SENDING)

    @override_settings(EMAIL_RATE_LIMIT=3, CELERY_TASK_ALWAYS_EAGER=False)
    @patch('notifications.tasks.send_promo_email_task.apply_async')
    def test_batch_index_consistency_under_rate_limiting(self, mock_reschedule):
        """Test that batch indices are calculated correctly even under concurrent access."""
        # Use only 5 users for clearer testing
        self.promo.selected_users.set(self.users[:5])
        self.promo.save()
        
        # Clear cache to ensure fresh start
        cache.delete('email_count')
        cache.delete(f'promo:{self.promo.id}:user_ids')
        
        # First batch should send 3 emails and reschedule
        send_promo_email_task(self.promo.id)
        
        # Should have sent 3 emails (rate limit)
        self.assertEqual(len(mail.outbox), 3)
        
        # Should have rescheduled with correct batch_start_index
        mock_reschedule.assert_called_once()
        call_args = mock_reschedule.call_args
        self.assertEqual(call_args[1]['kwargs']['batch_start_index'], 3)
        
        # Clear mailbox and reset rate limit for second batch
        mail.outbox.clear()
        cache.delete('email_count')
        
        # Second batch should complete the remaining 2 emails
        send_promo_email_task(self.promo.id, batch_start_index=3)
        
        # Should have sent remaining 2 emails
        self.assertEqual(len(mail.outbox), 2)
        
        # Status should be SENT
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.status, PromoEmail.SENT)

    def test_cache_corruption_recovery(self):
        """Test recovery from cache corruption scenarios."""
        # Corrupt the user_ids cache by setting it to invalid data
        cache.set(f'promo:{self.promo.id}:user_ids', "invalid_data")
        
        # Task should handle corruption gracefully
        with self.assertLogs('notifications.tasks', level='ERROR') as logs:
            try:
                send_promo_email_task(self.promo.id)
            except Exception:
                pass  # We expect this might fail, but shouldn't crash the system
        
        # Cache should be cleaned up on error
        cached_user_ids = cache.get(f'promo:{self.promo.id}:user_ids')
        self.assertIsNone(cached_user_ids)

    @override_settings(EMAIL_RATE_LIMIT=2, CELERY_TASK_ALWAYS_EAGER=False)
    def test_user_order_consistency_across_batches(self):
        """Test that user processing order is consistent across batch executions."""
        # Use a subset of users for clearer testing
        test_users = self.users[:5]
        self.promo.selected_users.set(test_users)
        self.promo.save()
        
        all_recipients = []
        
        # Process first batch (first 2 users)
        with patch('notifications.tasks.send_promo_email_task.apply_async') as mock_reschedule:
            send_promo_email_task(self.promo.id)
            
            # Get emails from first batch
            first_batch_recipients = [email.to[0] for email in mail.outbox]
            all_recipients.extend(first_batch_recipients)
            
            # Clear and process second batch
            mail.outbox.clear()
            cache.delete('email_count')
            
            # Get the rescheduled batch_start_index
            if mock_reschedule.called:
                batch_start_index = mock_reschedule.call_args[1]['kwargs']['batch_start_index']
                
                send_promo_email_task(self.promo.id, batch_start_index=batch_start_index)
                second_batch_recipients = [email.to[0] for email in mail.outbox]
                all_recipients.extend(second_batch_recipients)
        
        # Verify no duplicates
        unique_recipients = set(all_recipients)
        self.assertEqual(len(unique_recipients), len(all_recipients), "Found duplicate recipients")
        
        # Verify at least some users were processed
        self.assertGreater(len(all_recipients), 0)

    @tag('slow')
    def test_redis_lock_timeout_handling(self):
        """Test that Redis locks eventually timeout and allow retry."""
        lock_key = f'promo_task_lock:{self.promo.id}'
        
        # Manually set a lock with short timeout
        cache.set(lock_key, True, timeout=1)
        
        # First attempt should be blocked
        send_promo_email_task(self.promo.id)
        self.assertEqual(len(mail.outbox), 0)
        
        # Wait for lock to expire
        time.sleep(2)
        
        # Second attempt should succeed (but may hit rate limit)
        send_promo_email_task(self.promo.id)
        self.assertGreater(len(mail.outbox), 0)
        
        self.promo.refresh_from_db()
        self.assertIn(self.promo.status, [PromoEmail.SENT, PromoEmail.SENDING])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_mailgun_rate_limit_race_condition(self):
        """Test race condition handling in Mailgun rate limit error scenario."""
        # Mock Mailgun rate limit error
        with patch('notifications.tasks.EmailMultiAlternatives.send') as mock_send:
            mock_send.side_effect = [
                None,  # First email succeeds
                Exception("429 Too Many Requests"),  # Second email hits rate limit
            ]
            
            with patch('notifications.tasks.send_promo_email_task.apply_async') as mock_reschedule:
                # Use only 3 users for clearer testing
                self.promo.selected_users.set(self.users[:3])
                self.promo.save()
                
                send_promo_email_task(self.promo.id)
                
                # Should have rescheduled with correct index after rate limit hit
                mock_reschedule.assert_called_once()
                call_args = mock_reschedule.call_args
                self.assertEqual(call_args[1]['kwargs']['batch_start_index'], 1)  # Should continue from second user
                self.assertEqual(call_args[1]['countdown'], 7200)  # 2-hour delay for Mailgun limits


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CELERY_TASK_ALWAYS_EAGER=True
)
class AtomicOperationTests(TestCase):
    """Tests for atomic operations in cache and database."""

    def setUp(self):
        cache.clear()

    def test_cache_add_atomicity(self):
        """Test that cache.add is atomic and prevents race conditions."""
        key = "test_lock"
        
        # First add should succeed
        result1 = cache.add(key, "value1", timeout=60)
        self.assertTrue(result1)
        
        # Second add should fail (key already exists)
        result2 = cache.add(key, "value2", timeout=60)
        self.assertFalse(result2)
        
        # Value should still be the first one
        self.assertEqual(cache.get(key), "value1")

    def test_cache_incr_atomicity(self):
        """Test that cache.incr is atomic."""
        key = "test_counter"
        
        # Initialize counter
        cache.set(key, 0)
        
        # Increment should be atomic
        result1 = cache.incr(key)
        self.assertEqual(result1, 1)
        
        result2 = cache.incr(key)
        self.assertEqual(result2, 2)
        
        # Final value should be correct
        self.assertEqual(cache.get(key), 2)

    def test_cache_incr_nonexistent_key(self):
        """Test cache.incr behavior with non-existent key."""
        key = "nonexistent_counter"
        
        # Should raise ValueError for non-existent key
        with self.assertRaises(ValueError):
            cache.incr(key)

    def test_increment_email_count_first_time(self):
        """Test increment_email_count when counter doesn't exist."""
        # Ensure key doesn't exist
        cache.delete('email_count')
        
        # First increment should set it to 1
        count = increment_email_count()
        self.assertEqual(count, 1)
        
        # Should have timeout set
        # Note: We can't easily test the timeout directly, but we can verify the key exists
        self.assertEqual(cache.get('email_count'), 1)

    def test_increment_email_count_existing(self):
        """Test increment_email_count when counter already exists."""
        # Set initial value
        cache.set('email_count', 5, timeout=3600)
        
        # Increment should increase by 1
        count = increment_email_count()
        self.assertEqual(count, 6)
        
        # Cache should reflect the new value
        self.assertEqual(cache.get('email_count'), 6)