import hashlib

from django.test import TestCase

from hub.utils import shuffled_fast_participants
from tests.fixtures.test_data import TestDataFactory


class ShuffledFastParticipantsTest(TestCase):
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

    def test_order_is_deterministic_for_the_same_fast(self):
        first_pass = shuffled_fast_participants(1, self.profiles)
        second_pass = shuffled_fast_participants(1, self.profiles)

        self.assertEqual([p.id for p in first_pass], [p.id for p in second_pass])

    def test_order_is_independent_of_input_order(self):
        forward = shuffled_fast_participants(1, self.profiles)
        reversed_input = shuffled_fast_participants(1, list(reversed(self.profiles)))

        self.assertEqual([p.id for p in forward], [p.id for p in reversed_input])

    def test_order_differs_between_fasts(self):
        order_for_fast_1 = [p.id for p in shuffled_fast_participants(1, self.profiles)]
        order_for_fast_2 = [p.id for p in shuffled_fast_participants(2, self.profiles)]

        self.assertNotEqual(order_for_fast_1, order_for_fast_2)

    def test_matches_expected_md5_order_with_delimiter(self):
        # Pins the exact sort key so a regression to Python's built-in
        # hash() (random per-process) or a delimiter-less key format
        # (which would collide e.g. fast 1 + user 23 with fast 12 + user 3)
        # gets caught.
        expected = sorted(
            self.profiles,
            key=lambda p: (
                hashlib.md5(f"1-{p.user_id}".encode("utf-8")).hexdigest(),
                p.id,
            ),
        )

        result = shuffled_fast_participants(1, self.profiles)

        self.assertEqual([p.id for p in result], [p.id for p in expected])

    def test_returns_a_list_and_preserves_all_profiles(self):
        result = shuffled_fast_participants(1, self.profiles)

        self.assertIsInstance(result, list)
        self.assertEqual({p.id for p in result}, {p.id for p in self.profiles})
