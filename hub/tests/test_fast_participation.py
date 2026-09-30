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
                from django.db import connection

                self.connection = connection

        migration.backfill_fast_participation(self.apps, _NoopSchemaEditor())

    def test_replays_join_leave_pairs_into_closed_periods(self):
        self._make_event(
            self.profile,
            self.fast,
            self.EventType.USER_JOINED_FAST,
            timezone.now() - datetime.timedelta(days=10),
        )
        self._make_event(
            self.profile,
            self.fast,
            self.EventType.USER_LEFT_FAST,
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
            profile=self.profile,
            fast=self.fast,
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertIsNone(period.joined_at)
        self.assertIsNone(period.left_at)

    def test_current_membership_picks_up_latest_join_event_timestamp(self):
        """The newest matching join event is the one the open period inherits."""
        recent_join = self._make_event(
            self.profile,
            self.fast,
            self.EventType.USER_JOINED_FAST,
            timezone.now() - datetime.timedelta(days=2),
        )
        # Bypass the receiver so the backfill owns the open period.
        Profile.fasts.through.objects.create(
            profile=self.profile,
            fast=self.fast,
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertEqual(period.joined_at, recent_join.timestamp)

    def test_leave_without_prior_join_becomes_an_orphan_period(self):
        self._make_event(
            self.profile,
            self.fast,
            self.EventType.USER_LEFT_FAST,
            timezone.now(),
        )

        self._run_backfill()

        [period] = list(FastParticipation.objects.filter(profile=self.profile, fast=self.fast))
        self.assertIsNone(period.joined_at)
        self.assertIsNotNone(period.left_at)

    def test_current_membership_without_event_types_is_still_seeded(self):
        self.EventType.objects.filter(code__in=["user_joined_fast", "user_left_fast"]).delete()
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)

        self._run_backfill()

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertIsNone(period.joined_at)
        self.assertIsNone(period.left_at)

    def test_leave_only_event_type_still_reconstructs_history(self):
        self.EventType.objects.filter(code="user_joined_fast").delete()
        left_at = timezone.now()
        self._make_event(self.profile, self.fast, "user_left_fast", left_at)

        self._run_backfill()

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertIsNone(period.joined_at)
        self.assertEqual(period.left_at, left_at)

    def test_deleted_fast_target_is_ignored(self):
        event = self._make_event(self.profile, self.fast, "user_joined_fast", timezone.now())
        # A stale generic target can survive deletion; bypass live-model validation.
        self.Event.objects.filter(pk=event.pk).update(object_id=self.fast.pk + 1000000)

        self._run_backfill()

        self.assertFalse(FastParticipation.objects.exists())

    def test_historical_models_reconstruct_current_memberships(self):
        from importlib import import_module

        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        state = MigrationLoader(connection).project_state(
            [
                ("hub", "0070_fastparticipation"),
                ("events", "0014_drop_duplicate_analytics_indexes"),
            ]
        )
        migration = import_module("hub.migrations.0071_backfill_fastparticipation")
        from types import SimpleNamespace

        migration.backfill_fast_participation(state.apps, SimpleNamespace(connection=connection))

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertIsNone(period.joined_at)
        self.assertIsNone(period.left_at)

    def test_reverse_and_reapply_preserve_audit_rows(self):
        from importlib import import_module

        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self._run_backfill()
        before = list(FastParticipation.objects.values())
        migration = import_module("hub.migrations.0071_backfill_fastparticipation").Migration(
            "0071_backfill_fastparticipation", "hub"
        )
        state = MigrationLoader(connection).project_state([("hub", "0071_backfill_fastparticipation")])
        migration.unapply(state, connection.schema_editor())
        self.assertEqual(list(FastParticipation.objects.values()), before)
        self._run_backfill()
        self.assertEqual(list(FastParticipation.objects.values()), before)

    def test_missing_fast_content_type_does_not_skip_membership(self):
        self.ContentType.objects.filter(pk=self.fast_ct.pk).delete()
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)

        self._run_backfill()

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertIsNone(period.joined_at)
        self.assertIsNone(period.left_at)

    def test_historical_models_replay_events(self):
        from importlib import import_module
        from types import SimpleNamespace

        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        joined_at = timezone.now() - datetime.timedelta(days=2)
        left_at = timezone.now() - datetime.timedelta(days=1)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined_at)
        self._make_event(self.profile, self.fast, "user_left_fast", left_at)
        state = MigrationLoader(connection).project_state(
            [
                ("hub", "0070_fastparticipation"),
                ("events", "0014_drop_duplicate_analytics_indexes"),
            ]
        )
        migration = import_module("hub.migrations.0071_backfill_fastparticipation")
        migration.backfill_fast_participation(state.apps, SimpleNamespace(connection=connection))

        period = FastParticipation.objects.get(profile=self.profile, fast=self.fast)
        self.assertEqual(period.joined_at, joined_at)
        self.assertEqual(period.left_at, left_at)

    def test_migration_graph_serializes_the_pending_choices_fix(self):
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        self.assertNotIn("hub", loader.detect_conflicts())
        plan = loader.graph.forwards_plan(("hub", "0071_backfill_fastparticipation"))
        choices = ("hub", "0070_alter_llmprompt_model")
        schema = ("hub", "0070_fastparticipation")
        self.assertLess(plan.index(choices), plan.index(schema))

    def test_reapply_preserves_closed_history_and_current_open_period(self):
        joined_at = timezone.now() - datetime.timedelta(days=3)
        left_at = timezone.now() - datetime.timedelta(days=2)
        rejoined_at = timezone.now() - datetime.timedelta(days=1)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined_at)
        self._make_event(self.profile, self.fast, "user_left_fast", left_at)
        self._make_event(self.profile, self.fast, "user_joined_fast", rejoined_at)
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self._run_backfill()
        before = list(FastParticipation.objects.order_by("pk").values())
        self.assertEqual(len(before), 2)

        self._run_backfill()

        self.assertEqual(list(FastParticipation.objects.order_by("pk").values()), before)

    def test_reapply_preserves_identical_orphan_event_multiplicity(self):
        left_at = timezone.now()
        self._make_event(self.profile, self.fast, "user_left_fast", left_at)
        self._make_event(self.profile, self.fast, "user_left_fast", left_at)
        self._run_backfill()
        before = list(FastParticipation.objects.order_by("pk").values())
        self.assertEqual(len(before), 2)

        self._run_backfill()

        self.assertEqual(list(FastParticipation.objects.order_by("pk").values()), before)

    def test_replay_keeps_runtime_open_period_and_adds_missing_closed_history(self):
        joined_at = timezone.now() - datetime.timedelta(days=3)
        left_at = timezone.now() - datetime.timedelta(days=2)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined_at)
        self._make_event(self.profile, self.fast, "user_left_fast", left_at)
        self._make_event(self.profile, self.fast, "user_joined_fast", timezone.now())
        runtime_period = FastParticipation.objects.create(profile=self.profile, fast=self.fast, joined_at=None)

        self._run_backfill()
        self._run_backfill()

        runtime_period.refresh_from_db()
        self.assertIsNone(runtime_period.joined_at)
        self.assertIsNone(runtime_period.left_at)
        self.assertEqual(FastParticipation.objects.count(), 2)
        self.assertTrue(FastParticipation.objects.filter(joined_at=joined_at, left_at=left_at).exists())
