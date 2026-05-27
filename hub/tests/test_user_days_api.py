from datetime import date

from django.test import TestCase
from django.urls import resolve, reverse
from rest_framework import status

from hub.models import Day
from hub.views.day import UserDaysView
from tests.fixtures.test_data import TestDataFactory


class UserDaysViewTests(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="User Days Church")
        self.user = TestDataFactory.create_user(
            username="user-days@example.com",
            email="user-days@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.url = reverse("user-days")

    def test_mounted_url_resolves_to_user_days_view(self):
        match = resolve("/api/user/days/")

        self.assertEqual(match.func.view_class, UserDaysView)
        self.assertEqual(match.url_name, "user-days")
        self.assertEqual(self.url, "/api/user/days/")

    def test_user_days_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_days_returns_only_days_for_requesting_users_fasts(self):
        other_user = TestDataFactory.create_user(
            username="other-user-days@example.com",
            email="other-user-days@example.com",
            password="testpass123",
        )
        other_profile = TestDataFactory.create_profile(
            user=other_user,
            church=self.church,
        )
        joined_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Joined Fast",
        )
        other_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Other User Fast",
        )
        unjoined_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Unjoined Fast",
        )
        joined_days = [
            Day.objects.create(
                date=date(2026, 2, 1),
                church=self.church,
                fast=joined_fast,
            ),
            Day.objects.create(
                date=date(2026, 2, 2),
                church=self.church,
                fast=joined_fast,
            ),
        ]
        other_day = Day.objects.create(
            date=date(2026, 3, 1),
            church=self.church,
            fast=other_fast,
        )
        unjoined_day = Day.objects.create(
            date=date(2026, 4, 1),
            church=self.church,
            fast=unjoined_fast,
        )
        self.profile.fasts.add(joined_fast)
        other_profile.fasts.add(other_fast)

        self.client.force_login(self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        returned_days = response_data["results"]
        returned_ids = {item["id"] for item in returned_days}
        self.assertEqual(returned_ids, {day.id for day in joined_days})
        self.assertNotIn(other_day.id, returned_ids)
        self.assertNotIn(unjoined_day.id, returned_ids)
        self.assertTrue(all(item["fast"] == joined_fast.id for item in returned_days))
