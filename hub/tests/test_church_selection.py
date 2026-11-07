from django.contrib.auth.models import User
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from hub.models import Church, Day, Fast, Profile
from hub.services.church_selection import assign_only_church
from hub.tests.social_auth_helpers import SocialAuthTestMixin


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class ChurchSelectionTests(SocialAuthTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        Church.objects.all().delete()
        self.client = APIClient()

    def new_social_account(self, provider):
        response = self.client.post(
            "/api/auth/social/",
            self.social_payload(provider, f"{provider}-church-test", email=f"{provider}@example.test", name="Test User"),
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(pk=response.json()["user"]["id"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['tokens']['access']}")
        return user

    def test_new_apple_and_google_accounts_use_the_only_church(self):
        church = Church.objects.create(name="Only church")
        today = timezone.localdate()
        fast = Fast.objects.create(name="Visible church fast", church=church)
        fast.days.add(Day.objects.create(date=today, church=church))
        for provider in ("apple", "google"):
            with self.subTest(provider=provider):
                user = self.new_social_account(provider)
                self.assertEqual(user.profile.church_id, church.pk)
                for path in ("/api/fasts/", "/api/fasts/by-date/"):
                    response = self.client.get(path, {"church_id": 999999, "date": today.isoformat()})
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    items = payload["results"] if isinstance(payload, dict) else payload
                    self.assertEqual([item["id"] for item in items], [fast.pk])
                self.client.credentials()

    def test_existing_incomplete_profile_is_repaired_on_profile_read(self):
        user = self.new_social_account("google")
        self.assertIsNone(user.profile.church_id)
        church = Church.objects.create(name="Only church")
        response = self.client.get("/api/profile/")
        self.assertEqual(response.json()["church"], church.pk)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.church_id, church.pk)

    def test_existing_incomplete_profile_can_load_content_directly(self):
        user = self.new_social_account("apple")
        church = Church.objects.create(name="Only church")
        self.assertEqual(self.client.get("/api/fasts/").status_code, 200)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.church_id, church.pk)

    def test_multiple_churches_require_selection_for_both_providers(self):
        first = Church.objects.create(name="First church")
        second = Church.objects.create(name="Second church")
        for provider in ("apple", "google"):
            with self.subTest(provider=provider):
                user = self.new_social_account(provider)
                self.assertIsNone(user.profile.church_id)
                self.assertIsNone(self.client.get("/api/profile/").json()["church"])
                for path in ("/api/fasts/", "/api/fasts/by-date/", "/api/devotionals/by-date/", "/api/readings/", "/api/feasts/"):
                    response = self.client.get(path, {"church_id": first.pk})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["code"], "church_selection_required")
                response = self.client.patch("/api/profile/", {"church": second.pk}, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["church"], second.pk)
                self.assertEqual(self.client.get("/api/fasts/").status_code, 200)
                self.client.credentials()

    def test_zero_churches_do_not_create_or_guess_one(self):
        user = self.new_social_account("google")
        response = self.client.get("/api/fasts/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "church_selection_required")
        self.assertEqual(Church.objects.count(), 0)
        self.assertIsNone(user.profile.church_id)

    def test_stale_profile_does_not_overwrite_a_concurrent_selection(self):
        user = self.new_social_account("google")
        stale = user.profile
        first = Church.objects.create(name="Originally the only church")
        selected = Church.objects.create(name="Newly added church")
        Profile.objects.filter(pk=stale.pk).update(church=selected)
        with patch("hub.services.church_selection.only_church", return_value=first):
            assign_only_church(stale)
        self.assertEqual(stale.church_id, selected.pk)

    def test_existing_selection_is_preserved_when_another_church_is_added(self):
        first = Church.objects.create(name="First church")
        user = self.new_social_account("google")
        second = Church.objects.create(name="Second church")
        self.assertEqual(self.client.get("/api/profile/").json()["church"], first.pk)
        self.assertEqual(self.client.get("/api/fasts/", {"church_id": second.pk}).status_code, 200)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.church_id, first.pk)
