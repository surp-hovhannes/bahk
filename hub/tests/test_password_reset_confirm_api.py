from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status

from hub.views.user import PasswordResetConfirmView
from tests.fixtures.test_data import TestDataFactory


class PasswordResetConfirmAPITests(TestCase):
    def setUp(self):
        self.old_password = "oldpass123"
        self.new_password = "newpass456"
        self.user = TestDataFactory.create_user(
            username="reset-confirm@example.com",
            email="reset-confirm@example.com",
            password=self.old_password,
        )
        self.uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)
        self.url = reverse("password_reset_confirm")

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
        )

    def test_mounted_url_resolves_to_password_reset_confirm_view(self):
        match = resolve("/hub/password/reset/confirm/")

        self.assertEqual(match.func.view_class, PasswordResetConfirmView)
        self.assertEqual(match.url_name, "password_reset_confirm")

    def test_valid_token_resets_password(self):
        response = self._post(
            {
                "uidb64": self.uidb64,
                "token": self.token,
                "new_password": self.new_password,
                "confirm_password": self.new_password,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json()["detail"],
            "Password has been reset successfully.",
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))

    def test_invalid_token_is_rejected_without_changing_password(self):
        response = self._post(
            {
                "uidb64": self.uidb64,
                "token": "invalid-token",
                "new_password": self.new_password,
                "confirm_password": self.new_password,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))

    def test_missing_required_field_is_rejected_without_changing_password(self):
        response = self._post(
            {
                "uidb64": self.uidb64,
                "token": self.token,
                "new_password": self.new_password,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm_password", response.json())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))

    def test_password_mismatch_is_rejected_without_changing_password(self):
        response = self._post(
            {
                "uidb64": self.uidb64,
                "token": self.token,
                "new_password": self.new_password,
                "confirm_password": "different456",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))
