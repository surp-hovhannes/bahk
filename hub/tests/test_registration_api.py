from django.contrib.auth import get_user_model
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church, Profile
from hub.views.user import RegisterView


class RegistrationAPITests(APITestCase):
    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.url = reverse("auth_register")

    def _payload(self, email="new-user@example.com", password="StrongPass123!"):
        return {
            "email": email,
            "password": password,
            "password2": password,
            "profile": {
                "name": "New User",
                "church": self.church.id,
            },
        }

    def test_mounted_url_resolves_to_register_view(self):
        match = resolve("/hub/register/")

        self.assertEqual(match.func.view_class, RegisterView)
        self.assertEqual(match.url_name, "auth_register")

    def test_api_alias_resolves_and_serves_register_view(self):
        match = resolve("/api/register/")

        self.assertEqual(match.func.view_class, RegisterView)
        self.assertEqual(match.url_name, "auth_register")

        response = self.client.post("/api/register/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_anonymous_user_can_register(self):
        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("tokens", response.data)
        self.assertIn("access", response.data["tokens"])
        self.assertIn("refresh", response.data["tokens"])
        self.assertEqual(response.data["email"], "new-user@example.com")
        self.assertEqual(response.data["church"], self.church.id)

        user = get_user_model().objects.get(email="new-user@example.com")
        self.assertEqual(user.username, "new-user@example.com")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertTrue(
            Profile.objects.filter(
                user=user,
                name="New User",
                church=self.church,
            ).exists()
        )

    def test_registration_rejects_password_mismatch(self):
        payload = self._payload(email="mismatch@example.com")
        payload["password2"] = "DifferentPass123!"

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)
        self.assertFalse(
            get_user_model().objects.filter(email="mismatch@example.com").exists()
        )

    def test_registration_rejects_missing_required_fields(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.assertIn("password", response.data)
        self.assertIn("password2", response.data)
        self.assertIn("profile", response.data)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_registration_rejects_duplicate_email(self):
        get_user_model().objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            self.url,
            self._payload(email="existing@example.com"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.assertEqual(
            get_user_model().objects.filter(email="existing@example.com").count(),
            1,
        )
