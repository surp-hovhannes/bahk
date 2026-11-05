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

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from hub.models import Profile, SocialIdentity
from hub.services.accounts import issue_tokens_for_user
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
