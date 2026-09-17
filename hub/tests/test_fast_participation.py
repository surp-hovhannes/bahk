"""Tests for the FastParticipation join/leave history (issue #543).

Profile.fasts stays the membership source of truth; FastParticipation is the
audit log these tests pin.  Direct M2M mutation (profile.fasts.add/remove/clear)
is what JoinFastView/LeaveFastView call, so the receiver exercises that path
without the views.
"""

import datetime

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from tests.fixtures.test_data import TestDataFactory

from hub.models import Day, FastParticipation, Profile


def _days(fast, dates):
    for d in dates:
        Day.objects.create(date=d, church=fast.church, fast=fast)


class FastParticipationJoinLeaveTests(TestCase):
    """The m2m_changed receiver opens and stamps periods."""

    def setUp(self):
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user)
        self.fast = TestDataFactory.create_fast(church=self.profile.church)
        self.before = timezone.now()

    def test_join_opens_an_open_period(self):
        self.profile.fasts.add(self.fast)

        [period] = FastParticipation.objects.filter(profile=self.profile, fast=self.fast)
        self.assertIsNone(period.left_at)
        self.assertIsNotNone(period.joined_at)
        self.assertGreaterEqual(period.joined_at, self.before)

    def test_leave_stamps_left_at_on_the_open_period(self):
        self.profile.fasts.add(self.fast)
        before = timezone.now()

        self.profile.fasts.remove(self.fast)

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertIsNotNone(period.left_at)
        self.assertGreaterEqual(period.left_at, before)

    def test_rejoin_opens_a_new_period_and_preserves_the_old_one(self):
        """A completed-then-left fast must still look like a completion later."""
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        self.profile.fasts.add(self.fast)

        periods = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast).order_by("joined_at"))
        self.assertEqual(len(periods), 2)
        self.assertIsNotNone(periods[0].left_at)
        self.assertIsNone(periods[1].left_at)

    def test_clear_stamps_every_open_period(self):
        other_fast = TestDataFactory.create_fast(name="Other Fast", church=self.profile.church)
        self.profile.fasts.add(self.fast, other_fast)

        self.profile.fasts.clear()

        self.assertEqual(
            FastParticipation.objects.filter(profile=self.profile, left_at__isnull=True).count(),
            0,
        )

    def test_leave_without_a_prior_join_is_recorded_as_an_orphan_period(self):
        """A leave that finds no open period still leaves a ground-truth trail."""
        # Add a through row directly so the M2M is non-empty WITHOUT the
        # receiver seeing a join -- simulates a legacy backfill or an external
        # import.
        from hub.models import Profile

        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self.assertFalse(
            FastParticipation.objects.filter(profile=self.profile, fast=self.fast).exists(),
        )

        self.profile.fasts.remove(self.fast)

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertIsNone(period.joined_at)
        self.assertIsNotNone(period.left_at)

    def test_unique_constraint_rejects_two_open_periods_for_the_same_pair(self):
        FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=timezone.now(),
            left_at=None,
        )
        with self.assertRaises(IntegrityError):
            FastParticipation.objects.create(
                profile=self.profile,
                fast=self.fast,
                joined_at=timezone.now(),
                left_at=None,
            )


class FastParticipationCompletedPropertyTests(TestCase):
    """``completed`` distinguishes 'finished and left' from 'left early' from 'still in'."""

    def setUp(self):
        self.profile = TestDataFactory.create_profile()
        self.fast = TestDataFactory.create_fast(church=self.profile.church)
        today = timezone.localdate()

        _days(
            self.fast,
            [today - datetime.timedelta(days=d) for d in (4, 3, 2, 1)],
        )

    def test_open_period_is_completed_only_after_the_fast_ends(self):
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=timezone.now(),
            left_at=None,
        )
        self.assertTrue(period.completed)

    def test_open_period_is_not_completed_while_the_fast_still_has_days_left(self):
        Day.objects.create(
            date=timezone.localdate() + datetime.timedelta(days=1),
            church=self.fast.church,
            fast=self.fast,
        )
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=timezone.now(),
            left_at=None,
        )
        self.assertFalse(period.completed)

    def test_left_at_on_or_after_the_end_is_completed(self):
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=timezone.now(),
            left_at=timezone.now(),
        )
        self.assertTrue(period.completed)

    def test_left_at_before_the_end_is_an_early_departure(self):
        # Add a future day so left_at now is strictly before the fast ends.
        Day.objects.create(
            date=timezone.localdate() + datetime.timedelta(days=5),
            church=self.fast.church,
            fast=self.fast,
        )
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=timezone.now(),
            left_at=timezone.now(),
        )
        self.assertFalse(period.completed)

    def test_fast_with_no_days_is_never_completed(self):
        bare_fast = TestDataFactory.create_fast(name="Bare Fast", church=self.profile.church)
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=bare_fast,
            joined_at=timezone.now(),
            left_at=None,
        )
        self.assertFalse(period.completed)


class FastParticipationBackfillTests(TestCase):
    """The 0071 backfill reconstructs periods from the USER_JOINED/LEFT_FAST event log."""

    def setUp(self):
        from django.apps import apps
        from django.contrib.contenttypes.models import ContentType

        self.apps = apps
        self.ContentType = ContentType
        self.EventType = apps.get_model("events", "EventType")
        self.Event = apps.get_model("events", "Event")
        # Ensure event types are present (BaseTestCase does this in CI; standalone tests get it here).
        self.EventType.get_or_create_default_types()

        self.profile = TestDataFactory.create_profile()
        self.fast = TestDataFactory.create_fast(church=self.profile.church)
        self.fast_ct = ContentType.objects.get_for_model(apps.get_model("hub", "Fast"))

    def _make_event(self, profile, fast, code, timestamp):
        et = self.EventType.objects.get(code=code)
        return self.Event.objects.create(
            event_type=et,
            user=profile.user,
            content_type=self.fast_ct,
            object_id=fast.pk,
            timestamp=timestamp,
            title=f"{profile.user.username} {'joined' if code == self.EventType.USER_JOINED_FAST else 'left'} {fast.name}",
        )

    def _run_backfill(self):
        from importlib import import_module

        migration = import_module("hub.migrations.0071_backfill_fastparticipation")

        class _NoopSchemaEditor:
            def __init__(self, *args, **kwargs):
                pass

        migration.backfill_fast_participation(self.apps, _NoopSchemaEditor())

    def test_replays_join_leave_pairs_into_closed_periods(self):
        self._make_event(
            self.profile, self.fast, self.EventType.USER_JOINED_FAST,
            timezone.now() - datetime.timedelta(days=10),
        )
        self._make_event(
            self.profile, self.fast, self.EventType.USER_LEFT_FAST,
            timezone.now() - datetime.timedelta(days=5),
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertIsNotNone(period.joined_at)
        self.assertIsNotNone(period.left_at)
        self.assertGreater(period.left_at, period.joined_at)

    def test_current_membership_with_no_events_gets_a_null_joined_at(self):
        # Simulate pre-receiver data: the M2M was populated without the
        # receiver firing (legacy import), so no FastParticipation row exists.
        Profile.fasts.through.objects.create(
            profile=self.profile, fast=self.fast,
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertIsNone(period.joined_at)
        self.assertIsNone(period.left_at)

    def test_current_membership_picks_up_latest_join_event_timestamp(self):
        """The newest matching join event is the one the open period inherits."""
        recent_join = self._make_event(
            self.profile, self.fast, self.EventType.USER_JOINED_FAST,
            timezone.now() - datetime.timedelta(days=2),
        )
        # Bypass the receiver so the backfill owns the open period.
        Profile.fasts.through.objects.create(
            profile=self.profile, fast=self.fast,
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertEqual(period.joined_at, recent_join.timestamp)

    def test_leave_without_prior_join_becomes_an_orphan_period(self):
        self._make_event(
            self.profile, self.fast, self.EventType.USER_LEFT_FAST, timezone.now(),
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertIsNone(period.joined_at)
        self.assertIsNotNone(period.left_at)
