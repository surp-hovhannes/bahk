"""Token refresh edge cases.

#330: a refresh token whose account was deleted must yield a clean 401
(not a 500) so the client logs out instead of crashing.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class TokenRefreshTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gone", email="gone@example.test", password="pw12345!"
        )
        res = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "gone@example.test", "password": "pw12345!"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.refresh = res.json()["refresh"]
        self.refresh_url = reverse("token_refresh")

    def test_refresh_after_account_deletion_returns_401(self):
        self.user.delete()
        res = self.client.post(self.refresh_url, {"refresh": self.refresh})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.json()["code"], "token_not_valid")

    def test_refresh_with_valid_account_returns_200(self):
        res = self.client.post(self.refresh_url, {"refresh": self.refresh})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_refresh_with_garbage_token_returns_401(self):
        res = self.client.post(self.refresh_url, {"refresh": "garbage.token.here"})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)