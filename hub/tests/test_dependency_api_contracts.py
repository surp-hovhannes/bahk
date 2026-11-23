"""Rendered mobile API contracts across the Django/DRF security upgrades.

Use mounted endpoints, real JWT/session authentication and ordinary inputs.
Single-object writes must not acquire DRF's new per-item list error format.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from hub.models import Church, Profile


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dependency-api-contracts",
        }
    }
)
class DependencyAPIContractTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.password = "StrongPass123!"
        self.payload = {
            "email": "contracts@example.test",
            "password": self.password,
            "password2": self.password,
            "profile": {"name": "API Contract", "church": self.church.pk},
        }

    def register(self):
        response = self.client.post("/api/register/", self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_registration_login_refresh_and_default_authenticated_json(self):
        registration = self.register()
        self.assertEqual(registration["email"], self.payload["email"])
        self.assertEqual(registration["church"], self.church.pk)
        self.assertEqual(set(registration["tokens"]), {"access", "refresh"})

        login = self.client.post(
            "/api/token/",
            {"username": self.payload["email"], "password": self.password},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        refresh = self.client.post("/api/token/refresh/", {"refresh": login.json()["refresh"]}, format="json")
        self.assertEqual(refresh.status_code, 200, refresh.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.json()['access']}")

        # No Accept header: exercise the installed default renderer/negotiator.
        profile = self.client.get("/api/profile/")
        self.assertEqual(profile.status_code, 200, profile.content)
        self.assertTrue(profile["Content-Type"].startswith("application/json"))
        self.assertEqual(profile.json()["email"], self.payload["email"])
        self.assertEqual(profile.json()["name"], "API Contract")
        updated = self.client.patch("/api/profile/", {"name": "Updated Name"}, format="json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["name"], "Updated Name")
        self.assertEqual(Profile.objects.get().name, "Updated Name")

    def test_ordinary_utf8_json_and_accept_parameters_preserve_field_errors(self):
        registration = self.register()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {registration['tokens']['access']}")
        response = self.client.patch(
            "/api/profile/",
            '{"timezone": "not-a-timezone"}',
            content_type="application/json; charset=utf-8",
            HTTP_ACCEPT="application/json; indent=4, */*;q=0.1",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json(), {"timezone": ["Invalid timezone."]})

    def test_nested_registration_errors_keep_field_lists(self):
        self.payload["profile"] = {"name": "x" * 1000, "church": "not-an-id"}
        response = self.client.post("/api/register/", self.payload, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(set(response.json()), {"profile"})
        errors = response.json()["profile"]
        self.assertEqual(set(errors), {"name", "church"})
        for messages in errors.values():
            self.assertIsInstance(messages, list)
            self.assertTrue(messages)
            self.assertTrue(all(isinstance(message, str) for message in messages))
        self.assertFalse(get_user_model().objects.exists())

    def test_registration_rejects_bulk_lists_without_partial_creation(self):
        for payload in ([], [{}, self.payload, {"email": "invalid"}]):
            with self.subTest(payload=payload):
                response = self.client.post("/api/register/", payload, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(set(response.json()), {"non_field_errors"})
                self.assertIsInstance(response.json()["non_field_errors"], list)
                self.assertFalse(get_user_model().objects.exists())

    def test_registration_rejects_nested_lists_as_single_object_errors(self):
        for profile in ([], [{}, self.payload["profile"], {"name": ""}]):
            with self.subTest(profile=profile):
                payload = {**self.payload, "profile": profile}
                response = self.client.post("/api/register/", payload, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                errors = response.json()["profile"]
                self.assertEqual(set(errors), {"non_field_errors"})
                self.assertIsInstance(errors["non_field_errors"], list)
                self.assertFalse(get_user_model().objects.exists())

    def test_duplicate_email_error_keeps_field_list_and_existing_account(self):
        self.register()
        response = self.client.post("/api/register/", self.payload, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(set(response.json()), {"email"})
        self.assertIsInstance(response.json()["email"], list)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(Profile.objects.count(), 1)

    def test_session_authentication_still_requires_csrf_for_profile_writes(self):
        self.register()
        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=self.payload["email"], password=self.password))
        self.assertEqual(client.get("/api/profile/").status_code, 200)
        response = client.patch("/api/profile/", {"name": "Changed"}, format="json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertIn("CSRF", response.json()["detail"])
        self.assertEqual(Profile.objects.get().name, "API Contract")

    def test_partial_multipart_profile_update_retains_omitted_fields(self):
        registration = self.register()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {registration['tokens']['access']}")
        response = self.client.patch("/api/profile/", {"timezone": "America/New_York"}, format="multipart")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["timezone"], "America/New_York")
        self.assertEqual(response.json()["name"], "API Contract")
        profile = Profile.objects.get()
        self.assertEqual(profile.timezone, "America/New_York")
        self.assertEqual(profile.name, "API Contract")

    def test_generated_groups_relation_keeps_list_input_and_field_errors(self):
        # ModelSerializer builds this writable relation without explicit many=True.
        registration = self.register()
        user = get_user_model().objects.get(email=self.payload["email"])
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        group = Group.objects.create(name="Contract group")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {registration['tokens']['access']}")
        url = f"/api/users/{user.pk}/"
        group_url = f"/api/groups/{group.pk}/"

        updated = self.client.patch(url, {"groups": [group_url]}, format="json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(list(user.groups.values_list("pk", flat=True)), [group.pk])
        invalid = self.client.patch(url, {"groups": [group_url, "/api/groups/0/"]}, format="json")
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertEqual(set(invalid.json()), {"groups"})
        self.assertIsInstance(invalid.json()["groups"], list)
        self.assertEqual(list(user.groups.values_list("pk", flat=True)), [group.pk])

    def test_anonymous_permissions_and_success_list_pagination_stay_compatible(self):
        denied = self.client.get("/api/profile/")
        self.assertEqual(denied.status_code, 401, denied.content)
        self.assertIsInstance(denied.json()["detail"], str)
        churches = self.client.get("/api/churches/?limit=1")
        self.assertEqual(churches.status_code, 200, churches.content)
        body = churches.json()
        self.assertEqual(set(body), {"count", "next", "previous", "results"})
        self.assertIsInstance(body["results"], list)
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(set(body["results"][0]), {"id", "name"})
