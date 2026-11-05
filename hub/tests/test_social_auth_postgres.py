"""Real-concurrency races for social sign-in (issue #598). PostgreSQL only.

Run with ``--settings=tests.postgres_test_settings`` (CI job
``participation-postgres``). Each scenario fires simultaneous requests from
separate threads, each with its own DB connection, and checks the constraints
plus ``IntegrityError`` recovery leave exactly one account / identity and never
mint tokens for the wrong user.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from contextlib import contextmanager
from threading import local

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from hub.models import Profile, SocialIdentity
from hub.services.accounts import issue_tokens_for_user
from hub.services import accounts as account_services
from hub.services.social_auth import accounts as social_accounts
from hub.tests.social_auth_helpers import SocialAuthTestMixin
from tests.fixtures.test_data import TestDataFactory

User = get_user_model()
WORKERS = 4


@skipUnless(connection.vendor == "postgresql", "Real concurrent-insert races require PostgreSQL")
class SocialAuthPostgresRaceTests(SocialAuthTestMixin, TransactionTestCase):
    def run_concurrently(self, fn, payloads):
        barrier = Barrier(len(payloads))

        def worker(payload):
            try:
                barrier.wait(timeout=10)
                return fn(payload)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(worker, payloads))

    def test_simultaneous_first_sign_ins_create_one_account(self):
        # Distinct tokens (replay protection) for the same Google identity.
        payloads = [self.social_payload("google", "pg-race", email="race@example.com") for _ in range(WORKERS)]

        def sign_in(payload):
            response = APIClient().post("/api/auth/social/", payload, format="json")
            return response.status_code, response.json()

        results = self.run_concurrently(sign_in, payloads)
        self.assertEqual([status for status, _ in results], [200] * WORKERS, results)
        user_ids = {body["user"]["id"] for _, body in results}
        self.assertEqual(len(user_ids), 1)
        self.assertEqual(sum(body["is_new_user"] for _, body in results), 1)
        for _, body in results:
            self.assertEqual(str(AccessToken(body["tokens"]["access"])["user_id"]), str(body["user"]["id"]))
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Profile.objects.count(), 1)
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_simultaneous_links_of_one_identity_by_two_users(self):
        users = [
            TestDataFactory.create_user(username=f"pg{i}@example.com", email=f"pg{i}@example.com")
            for i in range(2)
        ]
        jobs = [
            (issue_tokens_for_user(user)["access"], self.social_payload("apple", "pg-contested"))
            for user in users
        ]

        def link(job):
            access, payload = job
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
            response = client.post("/api/auth/social/link/", payload, format="json")
            return response.status_code, response.json()

        results = self.run_concurrently(link, jobs)
        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses, [201, 409], results)
        loser = next(body for status, body in results if status == 409)
        self.assertEqual(loser["code"], "identity_in_use")
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_simultaneous_links_for_one_user_are_idempotent(self):
        user = TestDataFactory.create_user(username="pgsame@example.com", email="pgsame@example.com")
        access = issue_tokens_for_user(user)["access"]
        jobs = [(access, self.social_payload("google", "pg-same")) for _ in range(WORKERS)]

        def link(job):
            token, payload = job
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            return client.post("/api/auth/social/link/", payload, format="json").status_code

        statuses = self.run_concurrently(link, jobs)
        self.assertEqual(sorted(statuses), [200] * (WORKERS - 1) + [201], statuses)
        self.assertEqual(SocialIdentity.objects.filter(user=user).count(), 1)

    def run_signup_race(self, jobs):
        # Force every request to reach the email critical section together,
        # after serializer validation. Nested locks in the shared helper must
        # not wait at the test barrier a second time.
        barrier = Barrier(len(jobs))
        thread_state = local()
        real_lock = account_services.account_email_lock

        @contextmanager
        def simultaneous_lock(email):
            if not getattr(thread_state, "entered", False):
                thread_state.entered = True
                barrier.wait(timeout=10)
            with real_lock(email):
                yield

        def sign_up(job):
            url, payload = job
            response = APIClient().post(url, payload, format="json")
            return response.status_code, response.json()

        with (
            patch.object(account_services, "account_email_lock", simultaneous_lock),
            patch.object(social_accounts, "account_email_lock", simultaneous_lock),
        ):
            return self.run_concurrently(sign_up, jobs)

    def test_different_providers_cannot_claim_same_email_concurrently(self):
        email = "shared@example.com"
        jobs = [
            ("/api/auth/social/", self.social_payload(provider, f"shared-{provider}", email=email))
            for provider in ("apple", "google")
        ]
        results = self.run_signup_race(jobs)
        self.assertEqual(sorted(status for status, _ in results), [200, 409], results)
        self.assertEqual(next(body for status, body in results if status == 409)["code"], "account_link_required")
        self.assertEqual(User.objects.filter(email__iexact=email).count(), 1)
        self.assertEqual(Profile.objects.count(), 1)
        self.assertEqual(SocialIdentity.objects.count(), 1)
        self.assert_password_reset_and_login_work(email)

    def test_case_variants_share_the_email_lock(self):
        jobs = [
            ("/api/auth/social/", self.social_payload("apple", "case-apple", email="Shared@Example.com")),
            ("/api/auth/social/", self.social_payload("google", "case-google", email="shared@example.COM")),
        ]
        results = self.run_signup_race(jobs)
        self.assertEqual(sorted(status for status, _ in results), [200, 409], results)
        self.assertEqual(User.objects.filter(email__iexact="shared@example.com").count(), 1)

    def test_social_and_password_registration_cannot_claim_same_email(self):
        church = TestDataFactory.create_church()
        email = "mixed@example.com"
        jobs = [
            ("/api/auth/social/", self.social_payload("google", "mixed-google", email=email)),
            ("/api/register/", {
                "email": email, "password": "StrongPass123!", "password2": "StrongPass123!",
                "profile": {"name": "Mixed", "church": church.pk},
            }),
        ]
        results = self.run_signup_race(jobs)
        statuses = [status for status, _ in results]
        self.assertIn(statuses, ([200, 400], [409, 201]), results)
        self.assertEqual(User.objects.filter(email__iexact=email).count(), 1)
        self.assertEqual(Profile.objects.count(), 1)
        self.assert_password_reset_and_login_work(email)

    def test_relay_collision_keeps_separate_accounts_without_duplicate_login_email(self):
        email = "relay@privaterelay.appleid.com"
        jobs = [
            ("/api/auth/social/", self.social_payload("apple", subject, email=email, is_private_email=True))
            for subject in ("relay-one", "relay-two")
        ]
        results = self.run_signup_race(jobs)
        self.assertEqual([status for status, _ in results], [200, 200], results)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(User.objects.filter(email=email).count(), 1)
        self.assertEqual(User.objects.filter(email="").count(), 1)
        self.assertEqual(SocialIdentity.objects.count(), 2)

    def assert_password_reset_and_login_work(self, email):
        import re
        from django.core import mail

        client = APIClient()
        reset = client.post("/api/password/reset/", {"email": email}, format="json")
        self.assertEqual(reset.status_code, 200)
        message = mail.outbox[-1].alternatives[0][0]
        match = re.search(r"/reset-password/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", message)
        self.assertIsNotNone(match)
        password = "ChangedPass123!"
        confirmed = client.post("/api/password/reset/confirm/", {
            "uidb64": match[1], "token": match[2],
            "new_password": password, "confirm_password": password,
        }, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        login = client.post("/api/token/", {"username": email, "password": password}, format="json")
        self.assertEqual(login.status_code, 200, login.content)


@skipUnless(connection.vendor == "postgresql", "Requires real row locks")
class AppleRevocationPostgresTests(SocialAuthTestMixin, TransactionTestCase):
    def test_overlapping_workers_only_revoke_once(self):
        from threading import Event
        from unittest.mock import patch
        from django.test import override_settings
        from hub.models import PendingAppleRevocation
        from hub.services.social_auth.crypto import encrypt_payload
        from hub.services.social_auth.revocation import persist_revocations
        from hub.tasks.social_auth_tasks import process_pending_apple_revocations_task

        ids = persist_revocations([encrypt_payload({"client_id": "test-client", "refresh_token": "test-token"})])
        entered, release = Event(), Event()

        def revoke(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("Worker did not release")

        def worker():
            try:
                return process_pending_apple_revocations_task(ids)
            finally:
                connection.close()

        with (
            override_settings(APPLE_TEAM_ID="TEAM", APPLE_KEY_ID="KEY", APPLE_PRIVATE_KEY="configured"),
            patch.object(self.apple_client, "revoke_refresh_token", side_effect=revoke) as remote,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(timeout=10))
                second = pool.submit(worker)
                self.assertEqual(second.result(timeout=10), {"revoked": 0, "deferred": 0})
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), {"revoked": 1, "deferred": 0})
            self.assertEqual(remote.call_count, 1)
        self.assertFalse(PendingAppleRevocation.objects.exists())
