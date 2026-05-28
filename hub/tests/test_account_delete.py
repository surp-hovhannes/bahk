from django.contrib.auth import get_user_model
from django.urls import resolve, reverse
from django.test import TestCase
from rest_framework import status

from hub.models import Profile
from hub.views.user import DeleteAccountView
from tests.fixtures.test_data import TestDataFactory


User = get_user_model()


class DeleteAccountViewTests(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Account Delete Church")
        self.user = TestDataFactory.create_user(
            username="delete-me@example.com",
            email="delete-me@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.other_user = TestDataFactory.create_user(
            username="keep-me@example.com",
            email="keep-me@example.com",
            password="testpass123",
        )
        self.other_profile = TestDataFactory.create_profile(
            user=self.other_user,
            church=self.church,
        )
        self.url = reverse("account-delete")

    def test_mounted_hub_url_resolves_to_delete_account_view(self):
        match = resolve("/hub/account/delete/")

        self.assertEqual(match.func.view_class, DeleteAccountView)
        self.assertEqual(match.url_name, "account-delete")

    def test_delete_account_requires_authentication(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(Profile.objects.filter(pk=self.profile.pk).exists())

    def test_only_delete_method_is_allowed_for_authenticated_user(self):
        self.client.force_login(self.user)

        for method in (
            self.client.get,
            self.client.post,
            self.client.put,
            self.client.patch,
        ):
            response = method(self.url, data={}, content_type="application/json")
            self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_delete_removes_only_requesting_user_account(self):
        self.client.force_login(self.user)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Profile.objects.filter(pk=self.profile.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.other_user.pk).exists())
        self.assertTrue(Profile.objects.filter(pk=self.other_profile.pk).exists())
