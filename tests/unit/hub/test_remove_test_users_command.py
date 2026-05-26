from django.contrib.auth.models import User
from django.core.management import call_command

from tests.base import BaseTestCase


class RemoveTestUsersForFastCommandTests(BaseTestCase):
    def setUp(self):
        self.church = self.create_church()
        self.fast = self.create_fast(church=self.church)

    def _create_user_for_fast(self, username):
        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
        )
        profile = self.create_profile(user=user, church=self.church)
        profile.fasts.add(self.fast)
        return user

    def test_fast_scoped_delete_keeps_non_generated_usernames(self):
        generated_user = self._create_user_for_fast(
            f"testuser_1234abcd_{self.fast.id}_1@example.com"
        )
        real_user = self._create_user_for_fast("contestuser@example.com")

        call_command("remove_test_users_for_fast", fast_id=self.fast.id)

        self.assertFalse(User.objects.filter(pk=generated_user.pk).exists())
        self.assertTrue(User.objects.filter(pk=real_user.pk).exists())

    def test_all_test_users_delete_keeps_non_generated_usernames(self):
        generated_user = User.objects.create_user(
            username=f"testuser_1234abcd_{self.fast.id}_1@example.com",
            email=f"testuser_1234abcd_{self.fast.id}_1@example.com",
            password="testpass123",
        )
        real_user = User.objects.create_user(
            username="mytestuser@example.com",
            email="mytestuser@example.com",
            password="testpass123",
        )

        call_command("remove_test_users_for_fast", all_test_users=True)

        self.assertFalse(User.objects.filter(pk=generated_user.pk).exists())
        self.assertTrue(User.objects.filter(pk=real_user.pk).exists())
