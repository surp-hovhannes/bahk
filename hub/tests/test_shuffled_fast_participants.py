import hashlib
from datetime import date, timedelta

from django.db.models import QuerySet
from django.test import TestCase

from hub.models import Profile
from hub.utils import shuffled_fast_participants
from tests.fixtures.test_data import TestDataFactory


class ShuffledFastParticipantsTest(TestCase):
    rotation_date = date(2026, 8, 31)

    def setUp(self):
        self.church = TestDataFactory.create_church(name="Shuffle Church")
        self.profiles = [
            TestDataFactory.create_profile(
                user=TestDataFactory.create_user(
                    username=f"shuffle{i}@example.com",
                    email=f"shuffle{i}@example.com",
                    password="testpass123",
                ),
                church=self.church,
            )
            for i in range(5)
        ]
        self.queryset = Profile.objects.filter(id__in=[profile.id for profile in self.profiles])

    def _ordered_profiles(self, fast_id=1, rotation_date=None):
        return list(
            shuffled_fast_participants(
                fast_id,
                self.queryset,
                rotation_date or self.rotation_date,
            )
        )

    def test_order_is_deterministic_within_one_day(self):
        first_pass = self._ordered_profiles()
        second_pass = self._ordered_profiles()

        self.assertEqual([profile.id for profile in first_pass], [profile.id for profile in second_pass])

    def test_order_changes_on_the_next_day(self):
        today = self._ordered_profiles()
        tomorrow = self._ordered_profiles(rotation_date=self.rotation_date + timedelta(days=1))

        self.assertNotEqual([profile.id for profile in today], [profile.id for profile in tomorrow])

    def test_order_is_independent_of_input_order(self):
        forward = list(
            shuffled_fast_participants(
                1,
                self.queryset.order_by("id"),
                self.rotation_date,
            )
        )
        reverse = list(
            shuffled_fast_participants(
                1,
                self.queryset.order_by("-id"),
                self.rotation_date,
            )
        )

        self.assertEqual([profile.id for profile in forward], [profile.id for profile in reverse])

    def test_matches_expected_md5_order_with_fast_and_date_delimiters(self):
        expected = sorted(
            self.profiles,
            key=lambda profile: (
                hashlib.md5(
                    f"1-{self.rotation_date.isoformat()}-{profile.user_id}".encode("utf-8")
                ).hexdigest(),
                profile.id,
            ),
        )

        result = self._ordered_profiles()

        self.assertEqual([profile.id for profile in result], [profile.id for profile in expected])

    def test_returns_a_queryset_that_applies_limit_in_sql(self):
        result = shuffled_fast_participants(1, self.queryset, self.rotation_date)

        self.assertIsInstance(result, QuerySet)
        with self.assertNumQueries(1):
            limited_profiles = list(result[:2])

        self.assertEqual(len(limited_profiles), 2)
