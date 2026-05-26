from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from notifications.models import PromoEmail


class RetryFailedEmailsCommandTests(TestCase):
    def create_promo(self, status):
        return PromoEmail.objects.create(
            title=f"{status.title()} Promo",
            subject="Test Subject",
            content_html="<p>Test content</p>",
            content_text="Test content",
            status=status,
        )

    @patch("notifications.management.commands.retry_failed_emails.send_promo_email_task.delay")
    def test_specific_retry_rejects_non_failed_promos(self, mock_delay):
        for status in [
            PromoEmail.SENT,
            PromoEmail.SCHEDULED,
            PromoEmail.SENDING,
            PromoEmail.DRAFT,
        ]:
            with self.subTest(status=status):
                promo = self.create_promo(status)
                stdout = StringIO()

                call_command("retry_failed_emails", promo_id=promo.id, stdout=stdout)

                promo.refresh_from_db()
                self.assertEqual(promo.status, status)
                self.assertIn("only failed emails can be retried", stdout.getvalue())

        mock_delay.assert_not_called()

    @patch("notifications.management.commands.retry_failed_emails.send_promo_email_task.delay")
    def test_specific_retry_queues_failed_promo(self, mock_delay):
        promo = self.create_promo(PromoEmail.FAILED)
        stdout = StringIO()

        call_command("retry_failed_emails", promo_id=promo.id, stdout=stdout)

        promo.refresh_from_db()
        self.assertEqual(promo.status, PromoEmail.DRAFT)
        mock_delay.assert_called_once_with(promo.id)
        self.assertIn("Successfully queued retry", stdout.getvalue())
