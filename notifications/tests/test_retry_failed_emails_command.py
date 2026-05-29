from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from notifications.management.commands.retry_failed_emails import Command
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

    @patch("notifications.management.commands.retry_failed_emails.Command.claim_failed_promo")
    @patch("notifications.management.commands.retry_failed_emails.send_promo_email_task.delay")
    def test_specific_retry_skips_already_claimed_failed_promo(self, mock_delay, mock_claim):
        promo = self.create_promo(PromoEmail.FAILED)
        mock_claim.return_value = None
        stdout = StringIO()

        call_command("retry_failed_emails", promo_id=promo.id, stdout=stdout)

        mock_claim.assert_called_once_with(promo.id)
        mock_delay.assert_not_called()
        self.assertIn("already claimed", stdout.getvalue())

    def test_claim_failed_promo_only_transitions_failed_rows(self):
        failed_promo = self.create_promo(PromoEmail.FAILED)
        sent_promo = self.create_promo(PromoEmail.SENT)
        command = Command()

        claimed = command.claim_failed_promo(failed_promo.id)
        skipped = command.claim_failed_promo(sent_promo.id)

        failed_promo.refresh_from_db()
        sent_promo.refresh_from_db()
        self.assertEqual(claimed.id, failed_promo.id)
        self.assertEqual(failed_promo.status, PromoEmail.DRAFT)
        self.assertIsNone(skipped)
        self.assertEqual(sent_promo.status, PromoEmail.SENT)
