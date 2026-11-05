"""Account deletion must never discard an unconfirmed Apple revocation."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from hub.models import PendingAppleRevocation, SocialIdentity
from hub.services.accounts import issue_tokens_for_user
from hub.services.social_auth.apple import AppleTokenError
from hub.services.social_auth.crypto import encrypt_payload
from hub.services.social_auth.revocation import persist_revocations
from hub.tasks.social_auth_tasks import process_pending_apple_revocations_task, revoke_apple_tokens_task
from hub.tests.social_auth_helpers import APPLE_AUDIENCE, SocialAuthTestMixin


@override_settings(APPLE_TEAM_ID="TEAM", APPLE_KEY_ID="KEY", APPLE_PRIVATE_KEY="configured")
class DurableAppleRevocationTests(SocialAuthTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="revocation-user", password=None)
        self.ciphertext = encrypt_payload({"client_id": APPLE_AUDIENCE, "refresh_token": "fake-refresh"})
        self.identity = SocialIdentity.objects.create(
            user=self.user, provider="apple", provider_subject="revocation-user",
            apple_refresh_token_encrypted=self.ciphertext,
        )
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + issue_tokens_for_user(self.user)["access"])

    @override_settings(SOCIAL_AUTH_ENABLED=False)
    def test_broker_and_apple_outage_recovers_after_account_deletion(self):
        with (
            mock.patch.object(process_pending_apple_revocations_task, "apply_async", side_effect=ConnectionError),
            mock.patch.object(self.apple_client, "revoke_refresh_token", side_effect=AppleTokenError("http_503", retryable=True)) as revoke,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.api.delete("/api/account/delete/")
            self.assertEqual(response.status_code, 204)
            self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
            self.assertFalse(SocialIdentity.objects.exists())
            revoke.assert_not_called()  # Broker outage never invokes inline retries.
            row = PendingAppleRevocation.objects.get()
            self.assertEqual(row.token_encrypted, self.ciphertext)
            self.assertEqual(process_pending_apple_revocations_task(), {"revoked": 0, "deferred": 1})
            self.assertEqual(revoke.call_count, 1)
            row.refresh_from_db()
            self.assertEqual(row.attempts, 1)
            self.assertGreater(row.next_attempt_at, timezone.now())
            self.assertEqual(process_pending_apple_revocations_task(), {"revoked": 0, "deferred": 0})
            self.assertEqual(revoke.call_count, 1)
        PendingAppleRevocation.objects.update(next_attempt_at=timezone.now())
        self.assertEqual(process_pending_apple_revocations_task(), {"revoked": 1, "deferred": 0})
        self.assertEqual(self.apple_client.revoked, [("fake-refresh", APPLE_AUDIENCE)])
        self.assertFalse(PendingAppleRevocation.objects.exists())

    def test_deletion_rollback_restores_identity_and_removes_pending_work(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.user.delete()
                self.assertEqual(PendingAppleRevocation.objects.count(), 1)
                raise RuntimeError("rollback")
        self.assertEqual(callbacks, [])
        self.assertTrue(SocialIdentity.objects.filter(pk=self.identity.pk).exists())
        self.assertFalse(PendingAppleRevocation.objects.exists())
        self.assertEqual(self.apple_client.revoked, [])

    def test_persistence_failure_rolls_back_account_deletion(self):
        with transaction.atomic(), mock.patch(
            "hub.services.social_auth.revocation.persist_revocations", side_effect=DatabaseError("unavailable"),
        ):
            response = self.api.delete("/api/account/delete/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(SocialIdentity.objects.filter(pk=self.identity.pk).exists())

    def test_bulk_user_cascade_also_preserves_revocation(self):
        with self.captureOnCommitCallbacks(execute=False):
            User.objects.filter(pk=self.user.pk).delete()
        self.assertEqual(PendingAppleRevocation.objects.get().token_encrypted, self.ciphertext)

    def test_queue_receives_only_row_ids_after_commit(self):
        with mock.patch.object(process_pending_apple_revocations_task, "apply_async") as wake:
            with self.captureOnCommitCallbacks(execute=True):
                self.identity.delete()
                wake.assert_not_called()
            wake.assert_called_once_with(
                args=[[PendingAppleRevocation.objects.get().pk]], kwargs={"using": "default"}, retry=False,
            )
            self.assertNotIn(self.ciphertext, str(wake.call_args))

    def test_repeated_failures_keep_credential_beyond_old_retry_limit(self):
        persist_revocations([self.ciphertext])
        self.apple_client.fail_revoke = AppleTokenError("http_503", retryable=True)
        for _ in range(8):
            PendingAppleRevocation.objects.update(next_attempt_at=timezone.now())
            self.assertEqual(process_pending_apple_revocations_task()["deferred"], 1)
        row = PendingAppleRevocation.objects.get()
        self.assertEqual(row.attempts, 8)
        self.assertEqual(row.token_encrypted, self.ciphertext)
        self.assertLessEqual(row.next_attempt_at, timezone.now() + timedelta(hours=1))

    def test_permanent_error_is_retained_for_configuration_recovery(self):
        persist_revocations([self.ciphertext])
        self.apple_client.fail_revoke = AppleTokenError("invalid_client")
        self.assertEqual(process_pending_apple_revocations_task()["deferred"], 1)
        self.assertEqual(PendingAppleRevocation.objects.get().last_error, "apple_rejected")
        self.apple_client.fail_revoke = None
        PendingAppleRevocation.objects.update(next_attempt_at=timezone.now())
        self.assertEqual(process_pending_apple_revocations_task()["revoked"], 1)

    def test_crash_after_remote_success_keeps_retryable_work(self):
        ids = persist_revocations([self.ciphertext])
        with mock.patch.object(PendingAppleRevocation, "delete", side_effect=RuntimeError("worker crash")):
            with self.assertRaises(RuntimeError):
                process_pending_apple_revocations_task(ids)
        self.assertEqual(PendingAppleRevocation.objects.count(), 1)
        self.assertEqual(process_pending_apple_revocations_task(ids)["revoked"], 1)
        self.assertEqual(process_pending_apple_revocations_task(ids)["revoked"], 0)

    def test_batch_limit_and_duplicate_scheduling(self):
        first = persist_revocations([self.ciphertext, self.ciphertext])
        self.assertEqual(len(set(first)), 1)
        second = encrypt_payload({"client_id": APPLE_AUDIENCE, "refresh_token": "second"})
        persist_revocations([second])
        self.assertEqual(process_pending_apple_revocations_task(limit=1)["revoked"], 1)
        self.assertEqual(PendingAppleRevocation.objects.count(), 1)

    def test_status_command_is_read_only_and_hides_credentials(self):
        persist_revocations([self.ciphertext])
        output = StringIO()
        call_command("process_apple_revocations", "--status", stdout=output)
        self.assertIn('"pending": 1', output.getvalue())
        self.assertNotIn(self.ciphertext, output.getvalue())
        self.assertNotIn("fake-refresh", output.getvalue())
        self.assertEqual(self.apple_client.revoked, [])
        self.assertEqual(PendingAppleRevocation.objects.count(), 1)

    def test_legacy_message_retries_if_database_cannot_preserve_it(self):
        with (
            mock.patch("hub.tasks.social_auth_tasks.persist_revocations", side_effect=DatabaseError("schema not ready")),
            mock.patch.object(revoke_apple_tokens_task, "retry", side_effect=RuntimeError("retry queued")) as retry,
        ):
            with self.assertRaisesRegex(RuntimeError, "retry queued"):
                revoke_apple_tokens_task.apply(args=[[self.ciphertext]], throw=True)
        retry.assert_called_once()
        self.assertIsNone(revoke_apple_tokens_task.max_retries)
        self.assertTrue(revoke_apple_tokens_task.reject_on_worker_lost)

    def test_worker_waits_for_schema_during_rolling_deploy(self):
        with mock.patch("django.db.connection.introspection.table_names", return_value=[]):
            self.assertTrue(process_pending_apple_revocations_task()["waiting_for_migration"])
        self.assertEqual(self.apple_client.revoked, [])

    def test_beat_recovery_is_registered_independently_of_feature_flag(self):
        from bahk.celery import app

        entry = app.conf.beat_schedule["retry-pending-apple-revocations"]
        self.assertEqual(entry["task"], process_pending_apple_revocations_task.name)


class SocialIdentityReadOnlyAdminTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="social-only", password=None)
        self.identity = SocialIdentity.objects.create(user=self.owner, provider="apple", provider_subject="sole")
        self.admin_user = User.objects.create_superuser(username="admin", email="admin@example.com", password="Admin123!")
        self.client.force_login(self.admin_user, backend="hub.auth.EmailBackend")

    def test_admin_cannot_delete_last_identity(self):
        response = self.client.post(reverse("admin:hub_socialidentity_delete", args=[self.identity.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SocialIdentity.objects.filter(pk=self.identity.pk).exists())

    def test_bulk_delete_action_is_unavailable_and_view_is_read_only(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        model_admin = admin.site._registry[SocialIdentity]
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertNotIn("delete_selected", model_admin.get_actions(request))
        response = self.client.post(reverse("admin:hub_socialidentity_changelist"), {
            "action": "delete_selected", "_selected_action": [self.identity.pk], "post": "yes",
        })
        self.assertIn(response.status_code, [200, 302])
        self.assertTrue(SocialIdentity.objects.filter(pk=self.identity.pk).exists())
