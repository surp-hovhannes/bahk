"""Tests for the FastParticipation join/leave history (issue #543).

Profile.fasts stays the membership source of truth; FastParticipation is the
audit log these tests pin.  Direct M2M mutation (profile.fasts.add/remove/clear)
is what JoinFastView/LeaveFastView call, so the receiver exercises that path
without the views.
"""

import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import IntegrityError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
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

    def test_runtime_leave_without_audited_join_does_not_invent_an_orphan(self):
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self.profile.fasts.remove(self.fast)
        self.assertFalse(FastParticipation.objects.filter(profile=self.profile, fast=self.fast).exists())

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
            joined_at=timezone.now() - datetime.timedelta(days=3),
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
            joined_at=timezone.now() - datetime.timedelta(days=3),
            left_at=None,
        )
        self.assertFalse(period.completed)

    def test_left_at_on_or_after_the_end_is_completed(self):
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=timezone.now() - datetime.timedelta(days=3),
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
            joined_at=timezone.now() - datetime.timedelta(days=3),
            left_at=timezone.now(),
        )
        self.assertFalse(period.completed)

    def test_fast_with_no_days_is_never_completed(self):
        bare_fast = TestDataFactory.create_fast(name="Bare Fast", church=self.profile.church)
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=bare_fast,
            joined_at=timezone.now() - datetime.timedelta(days=3),
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

    def test_initial_participation_migration_creates_final_history_schema(self):
        from django.db.migrations.loader import MigrationLoader
        from django.db.models import Q
        from django.db.models.deletion import SET_NULL

        loader = MigrationLoader(connection)
        state = loader.project_state([("hub", "0070_fastparticipation")])
        period = state.apps.get_model("hub", "FastParticipation")
        fast = period._meta.get_field("fast")
        self.assertTrue(fast.null)
        self.assertIs(fast.remote_field.on_delete, SET_NULL)
        self.assertEqual(period._meta.get_field("ended_at_unknown").db_default, False)
        self.assertEqual(period._meta.get_field("fast_name").db_default, "")
        for name in ("fast_original_id", "fast_year", "fast_end_date", "fast_deleted_at"):
            self.assertIsNotNone(period._meta.get_field(name))
        constraint = period._meta.constraints[0]
        self.assertEqual(constraint.condition, Q(left_at__isnull=True, ended_at_unknown=False))
        self.assertNotIn(("hub", "0072_fastparticipation_history_snapshots"), loader.disk_migrations)

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
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        runtime_period = FastParticipation.objects.create(profile=self.profile, fast=self.fast, joined_at=None)

        self._run_backfill()
        self._run_backfill()

        runtime_period.refresh_from_db()
        self.assertIsNone(runtime_period.joined_at)
        self.assertIsNone(runtime_period.left_at)
        self.assertEqual(FastParticipation.objects.count(), 2)
        self.assertTrue(FastParticipation.objects.filter(joined_at=joined_at, left_at=left_at).exists())


class AndyReviewBaselineTests(TestCase):
    def setUp(self):
        self.profile = TestDataFactory.create_profile()
        self.fast = TestDataFactory.create_fast(church=self.profile.church)
        _days(self.fast, [datetime.date(2026, 10, 1)])

    def test_nonmember_and_repeated_remove_do_not_invent_history(self):
        self.profile.fasts.remove(self.fast)
        self.assertFalse(FastParticipation.objects.exists())
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        self.profile.fasts.remove(self.fast)
        self.assertEqual(FastParticipation.objects.count(), 1)

    def test_post_end_join_is_not_completion(self):
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=datetime.datetime(2026, 10, 11, 12, tzinfo=datetime.timezone.utc),
        )
        self.assertFalse(period.completed)

    def test_database_utc_leave_on_previous_local_day_is_early(self):
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=datetime.datetime(2026, 9, 28, 12, tzinfo=datetime.timezone.utc),
            left_at=datetime.datetime(2026, 10, 1, 1, tzinfo=datetime.timezone.utc),
        )
        period.refresh_from_db()
        self.assertFalse(period.completed)

    def test_fast_deletion_preserves_history(self):
        period = FastParticipation.objects.create(
            profile=self.profile,
            fast=self.fast,
            joined_at=datetime.datetime(2026, 9, 28, 12, tzinfo=datetime.timezone.utc),
        )
        self.fast.delete()
        self.assertTrue(FastParticipation.objects.filter(pk=period.pk).exists())


class CompletionAndDeletionReviewTests(TestCase):
    def setUp(self):
        self.profile = TestDataFactory.create_profile()
        self.fast = TestDataFactory.create_fast(church=self.profile.church)
        _days(self.fast, [datetime.date(2026, 9, 28), datetime.date(2026, 10, 1)])

    def period(self, joined, left=None, **extra):
        return FastParticipation.objects.create(
            profile=self.profile, fast=self.fast, joined_at=joined, left_at=left, **extra
        )

    def test_final_local_day_mid_fast_join_and_queryset_parity(self):
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        local = ZoneInfo("America/Los_Angeles")
        joined = datetime.datetime(2026, 9, 30, 18, tzinfo=local)
        for hour in (0, 12, 23):
            period = self.period(joined, datetime.datetime(2026, 10, 1, hour, tzinfo=local))
            period.refresh_from_db()
            self.assertTrue(period.completed)
        self.period(joined, datetime.datetime(2026, 9, 30, 23, tzinfo=local))
        self.period(None, datetime.datetime(2026, 10, 2, 12, tzinfo=local))
        self.period(joined, ended_at_unknown=True)
        self.period(datetime.datetime(2026, 10, 2, 12, tzinfo=local))
        expected = {p.pk for p in FastParticipation.objects.all() if p.completed}
        self.assertEqual(len(expected), 3)
        with self.assertNumQueries(1):
            annotated = list(FastParticipation.objects.with_completion())
            self.assertEqual({p.pk for p in annotated if p.completed}, expected)
        self.assertEqual(set(FastParticipation.objects.completed().values_list("pk", flat=True)), expected)
        FastParticipation.objects.filter(left_at__isnull=True, ended_at_unknown=False).delete()
        period = self.period(joined)
        with patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 1)):
            self.assertFalse(period.completed)
        with patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 2)):
            self.assertTrue(period.completed)

    def test_deleted_unfinished_fast_never_completes_later(self):
        from unittest.mock import patch

        period = self.period(datetime.datetime(2026, 9, 28, 12, tzinfo=datetime.timezone.utc))
        original_id = self.fast.pk
        deletion = datetime.datetime(2026, 9, 30, 12, tzinfo=datetime.timezone.utc)
        with patch("django.utils.timezone.now", return_value=deletion):
            self.fast.delete()
        period.refresh_from_db()
        self.assertIsNone(period.fast_id)
        self.assertEqual(period.fast_name, self.fast.name)
        self.assertEqual(period.fast_end_date, datetime.date(2026, 10, 1))
        self.assertEqual(period.fast_deleted_at, deletion)
        self.assertEqual(period.fast_original_id, original_id)
        self.assertEqual(FastParticipation.objects.filter(fast_original_id=original_id).count(), 1)
        self.assertFalse(
            FastParticipation.objects.with_completion(as_of=datetime.date(2030, 1, 1)).get(pk=period.pk).completed
        )

    def test_deleted_completed_fast_retains_completion_and_profile_delete_erases_it(self):
        from unittest.mock import patch

        period = self.period(datetime.datetime(2026, 9, 28, 12, tzinfo=datetime.timezone.utc))
        with patch(
            "django.utils.timezone.now", return_value=datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.timezone.utc)
        ):
            self.fast.delete()
        period.refresh_from_db()
        self.assertTrue(period.completed)
        from django.core.management import call_command
        from io import StringIO

        call_command("reconcile_fast_participations", stdout=StringIO())
        period.refresh_from_db()
        self.assertFalse(period.ended_at_unknown)
        self.assertTrue(period.completed)
        self.profile.delete()
        self.assertFalse(FastParticipation.objects.filter(pk=period.pk).exists())

    def test_bulk_fast_delete_preserves_snapshot(self):
        from hub.models import Fast

        period = self.period(None)
        Fast.objects.filter(pk=self.fast.pk).delete()
        period.refresh_from_db()
        self.assertIsNone(period.fast_id)
        self.assertEqual(period.fast_name, self.fast.name)

    def test_existing_join_timestamp_is_never_overwritten(self):
        joined = timezone.now() - datetime.timedelta(days=2)
        period = self.period(joined)
        self.profile.fasts.add(self.fast)
        period.refresh_from_db()
        self.assertEqual(period.joined_at, joined)

    def test_reverse_clear_and_remove_only_close_real_members(self):
        self.fast.profiles.remove(self.profile)
        self.assertFalse(FastParticipation.objects.exists())
        self.fast.profiles.add(self.profile)
        self.fast.profiles.clear()
        self.fast.profiles.remove(self.profile)
        self.assertEqual(FastParticipation.objects.count(), 1)
        self.assertIsNotNone(FastParticipation.objects.get().left_at)


class ReconciliationReviewTests(FastParticipationBackfillTests):
    def test_repeated_join_events_never_invent_a_leave_or_confirm_completion(self):
        joined = timezone.now() - datetime.timedelta(days=3)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined + datetime.timedelta(days=1))
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self._run_backfill()
        period = FastParticipation.objects.get()
        self.assertIsNone(period.joined_at)
        self.assertIsNone(period.left_at)
        self.assertFalse(period.completed)

    def test_real_live_closed_periods_do_not_duplicate_on_reconciliation(self):
        from django.core.management import call_command
        from io import StringIO

        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        before = list(FastParticipation.objects.values())
        self.assertEqual(len(before), 1)
        call_command("reconcile_fast_participations", stdout=StringIO())
        call_command("reconcile_fast_participations", stdout=StringIO())
        self.assertEqual(list(FastParticipation.objects.values()), before)

    def test_reconciliation_skips_new_noop_removal_events(self):
        from django.core.management import call_command
        from io import StringIO

        self.profile.fasts.remove(self.fast)
        self.assertEqual(FastParticipation.objects.count(), 0)
        call_command("reconcile_fast_participations", stdout=StringIO())
        self.assertEqual(FastParticipation.objects.count(), 0)
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        self.profile.fasts.remove(self.fast)
        before = list(FastParticipation.objects.values())
        call_command("reconcile_fast_participations", stdout=StringIO())
        self.assertEqual(list(FastParticipation.objects.values()), before)
        self.assertEqual(len(before), 1)

    def test_real_membership_events_reference_the_existing_period(self):
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        period = FastParticipation.objects.get()
        events = self.Event.objects.filter(
            user=self.profile.user, object_id=self.fast.pk, event_type__code__in=["user_joined_fast", "user_left_fast"]
        )
        self.assertEqual(events.count(), 2)
        for event in events:
            self.assertTrue(event.data["participation_tracked"])
            self.assertTrue(event.data["participation_changed"])
            self.assertEqual(event.data["participation_id"], period.pk)

    def test_legacy_unlinked_independently_timed_closed_history_is_not_duplicated(self):
        from django.core.management import call_command
        from io import StringIO

        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        self.Event.objects.filter(user=self.profile.user, object_id=self.fast.pk).update(data={})
        before = list(FastParticipation.objects.values())
        call_command("reconcile_fast_participations", stdout=StringIO())
        call_command("reconcile_fast_participations", stdout=StringIO())
        self.assertEqual(list(FastParticipation.objects.values()), before)

    def test_linked_independently_timed_unknown_period_cannot_date_new_membership(self):
        self.profile.fasts.add(self.fast)
        old = FastParticipation.objects.get()
        old.ended_at_unknown = True
        old.save(update_fields=["ended_at_unknown"])
        self._run_backfill()
        current = FastParticipation.objects.get(ended_at_unknown=False, left_at__isnull=True)
        self.assertIsNone(current.joined_at)
        self.assertFalse(current.completed)

    def test_unlinked_independently_timed_unknown_period_cannot_date_new_membership(self):
        self.profile.fasts.add(self.fast)
        old = FastParticipation.objects.get()
        old.ended_at_unknown = True
        old.save(update_fields=["ended_at_unknown"])
        self.Event.objects.filter(user=self.profile.user, object_id=self.fast.pk).update(data={})
        self._run_backfill()
        current = FastParticipation.objects.get(ended_at_unknown=False, left_at__isnull=True)
        self.assertIsNone(current.joined_at)
        self.assertFalse(current.completed)

    def test_closed_existing_period_consumes_stale_join_evidence(self):
        joined = timezone.now() - datetime.timedelta(days=3)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined)
        FastParticipation.objects.create(
            profile=self.profile, fast=self.fast, joined_at=joined, left_at=joined + datetime.timedelta(days=1)
        )
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self._run_backfill()
        self.assertIsNone(FastParticipation.objects.get(left_at__isnull=True).joined_at)

    def test_stale_event_cannot_date_an_unknown_current_rejoin(self):
        joined = timezone.now() - datetime.timedelta(days=3)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined)
        FastParticipation.objects.create(profile=self.profile, fast=self.fast, joined_at=joined, ended_at_unknown=True)
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self._run_backfill()
        current = FastParticipation.objects.get(ended_at_unknown=False, left_at__isnull=True)
        self.assertIsNone(current.joined_at)
        self.assertFalse(current.completed)

    def test_stale_join_event_for_nonmember_does_not_make_open_period(self):
        self._make_event(self.profile, self.fast, "user_joined_fast", timezone.now())
        self._run_backfill()
        self.assertFalse(FastParticipation.objects.filter(left_at__isnull=True, ended_at_unknown=False).exists())

    def test_existing_stale_open_period_marks_unknown_end_without_inventing_timestamp(self):
        period = FastParticipation.objects.create(profile=self.profile, fast=self.fast, joined_at=timezone.now())
        self._run_backfill()
        period.refresh_from_db()
        self.assertTrue(period.ended_at_unknown)
        self.assertIsNone(period.left_at)
        self.assertFalse(period.completed)

    def test_rerunnable_command_recovers_deployment_gap_and_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO

        joined = timezone.now() - datetime.timedelta(days=2)
        self._make_event(self.profile, self.fast, "user_joined_fast", joined)
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        call_command("reconcile_fast_participations", stdout=StringIO())
        period = FastParticipation.objects.get()
        self.assertEqual(period.joined_at, joined)
        before = list(FastParticipation.objects.values())
        call_command("reconcile_fast_participations", stdout=StringIO())
        self.assertEqual(list(FastParticipation.objects.values()), before)

    def test_closed_event_history_does_not_guess_a_current_rejoin_date(self):
        self._make_event(self.profile, self.fast, "user_joined_fast", timezone.now() - datetime.timedelta(days=2))
        self._make_event(self.profile, self.fast, "user_left_fast", timezone.now() - datetime.timedelta(days=1))
        Profile.fasts.through.objects.create(profile=self.profile, fast=self.fast)
        self._run_backfill()
        self.assertIsNone(FastParticipation.objects.get(left_at__isnull=True).joined_at)


@skipUnless(connection.vendor == "postgresql", "Real row-lock races require PostgreSQL")
@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class FastParticipationPostgresConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.profile = TestDataFactory.create_profile()
        self.fast = TestDataFactory.create_fast(church=self.profile.church)

    def race(self, operation):
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                profile = Profile.objects.get(pk=self.profile.pk)
                barrier.wait(timeout=10)
                operation(profile)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(worker) for _ in range(2)]
            for result in results:
                result.result(timeout=20)

    def test_concurrent_joins_create_one_period_preserving_first_timestamp(self):
        self.race(lambda profile: profile.fasts.add(self.fast.pk))
        self.assertEqual(FastParticipation.objects.count(), 1)
        period = FastParticipation.objects.get()
        joined = period.joined_at
        self.race(lambda profile: profile.fasts.add(self.fast.pk))
        period.refresh_from_db()
        self.assertEqual(period.joined_at, joined)
        self.assertEqual(Profile.fasts.through.objects.count(), 1)

    def test_concurrent_leaves_create_no_false_periods(self):
        self.profile.fasts.add(self.fast)
        self.race(lambda profile: profile.fasts.remove(self.fast.pk))
        self.assertEqual(FastParticipation.objects.count(), 1)
        self.assertIsNotNone(FastParticipation.objects.get().left_at)
        self.assertFalse(Profile.fasts.through.objects.exists())

    def test_concurrent_clears_create_no_false_periods(self):
        self.profile.fasts.add(self.fast)
        self.race(lambda profile: profile.fasts.clear())
        self.assertEqual(FastParticipation.objects.count(), 1)
        self.assertIsNotNone(FastParticipation.objects.get().left_at)

    def test_join_and_leave_race_matches_membership(self):
        barrier = Barrier(2)

        def worker(join):
            close_old_connections()
            try:
                profile = Profile.objects.get(pk=self.profile.pk)
                barrier.wait(timeout=10)
                if join:
                    profile.fasts.add(self.fast.pk)
                else:
                    profile.fasts.remove(self.fast.pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker, join) for join in (True, False)]
            for job in jobs:
                job.result(timeout=20)
        member = self.profile.fasts.filter(pk=self.fast.pk).exists()
        self.assertEqual(
            FastParticipation.objects.filter(left_at__isnull=True, ended_at_unknown=False).count(), int(member)
        )
        self.assertFalse(FastParticipation.objects.filter(joined_at__isnull=True).exists())

    def test_reverse_clear_and_new_profile_join_race_matches_membership(self):
        from hub.models import Fast

        barrier = Barrier(2)

        def worker(join):
            close_old_connections()
            try:
                fast = Fast.objects.get(pk=self.fast.pk)
                barrier.wait(timeout=10)
                if join:
                    fast.profiles.add(self.profile.pk)
                else:
                    fast.profiles.clear()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker, join) for join in (True, False)]
            for job in jobs:
                job.result(timeout=20)
        member = self.profile.fasts.filter(pk=self.fast.pk).exists()
        self.assertEqual(
            FastParticipation.objects.filter(left_at__isnull=True, ended_at_unknown=False).count(), int(member)
        )

    def test_bulk_delete_and_remove_use_fast_before_participation_lock_order(self):
        from threading import Event as ThreadEvent
        from django.db import transaction
        from hub.models import Fast

        locked = ThreadEvent()
        deletion_attempted = ThreadEvent()
        self.profile.fasts.add(self.fast)

        def remove():
            close_old_connections()
            try:
                with transaction.atomic():
                    Fast.objects.select_for_update().get(pk=self.fast.pk)
                    locked.set()
                    self.assertTrue(deletion_attempted.wait(timeout=10))
                    Profile.objects.get(pk=self.profile.pk).fasts.remove(self.fast.pk)
            finally:
                close_old_connections()

        def delete():
            close_old_connections()

            def observe(execute, sql, params, many, context):
                if '"hub_fast"' in sql and "FOR UPDATE" in sql:
                    deletion_attempted.set()
                result = execute(sql, params, many, context)
                if sql.startswith('UPDATE "hub_fastparticipation"'):
                    # On the old order, this signals only after audit rows are
                    # locked, deterministically exposing the inversion.
                    deletion_attempted.set()
                return result

            try:
                self.assertTrue(locked.wait(timeout=10))
                with connection.execute_wrapper(observe):
                    Fast.objects.filter(pk=self.fast.pk).delete()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(remove), pool.submit(delete)]
            for job in jobs:
                job.result(timeout=25)
        period = FastParticipation.objects.get()
        self.assertIsNone(period.fast_id)
        self.assertIsNotNone(period.left_at)
        self.assertEqual(period.fast_original_id, self.fast.pk)


class SnapshotBackfillReviewTests(TestCase):
    def test_existing_history_gets_snapshots_and_unknown_end_without_timestamp_changes(self):
        from django.apps import apps
        from importlib import import_module
        from types import SimpleNamespace

        profile = TestDataFactory.create_profile()
        fast = TestDataFactory.create_fast(church=profile.church)
        end = timezone.localdate() - datetime.timedelta(days=1)
        _days(fast, [end])
        joined = timezone.now() - datetime.timedelta(days=3)
        period = FastParticipation.objects.create(profile=profile, fast=fast, joined_at=joined)
        migration = import_module("hub.services.fast_participation_backfill")
        migration.backfill_fast_participation(apps, SimpleNamespace(connection=connection))
        period.refresh_from_db()
        self.assertEqual(period.fast_name, fast.name)
        self.assertEqual(period.fast_original_id, fast.pk)
        self.assertEqual(period.fast_end_date, end)
        self.assertEqual(period.joined_at, joined)
        self.assertIsNone(period.left_at)
        self.assertTrue(period.ended_at_unknown)
        self.assertFalse(period.completed)
        before = list(FastParticipation.objects.values())
        migration.backfill_fast_participation(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(FastParticipation.objects.values()), before)

    def test_snapshot_backfill_keeps_real_current_membership_open(self):
        from django.apps import apps
        from importlib import import_module
        from types import SimpleNamespace

        profile = TestDataFactory.create_profile()
        fast = TestDataFactory.create_fast(church=profile.church)
        profile.fasts.add(fast)
        migration = import_module("hub.services.fast_participation_backfill")
        migration.backfill_fast_participation(apps, SimpleNamespace(connection=connection))
        period = FastParticipation.objects.get()
        self.assertFalse(period.ended_at_unknown)
        self.assertIsNone(period.left_at)


class ParticipationMigrationExecutorTests(TransactionTestCase):
    def test_legacy_membership_and_events_upgrade_to_final_schema(self):
        from django.contrib.contenttypes.models import ContentType
        from django.db.migrations.executor import MigrationExecutor
        from events.models import Event, EventType

        profile = TestDataFactory.create_profile()
        fast = TestDataFactory.create_fast(church=profile.church)
        end = timezone.localdate() - datetime.timedelta(days=1)
        _days(fast, [end])
        executor = MigrationExecutor(connection)
        final_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("hub", "0070_alter_llmprompt_model")])
            Profile.fasts.through.objects.create(profile=profile, fast=fast)
            EventType.get_or_create_default_types()
            joined = timezone.now() - datetime.timedelta(days=3)
            Event.objects.create(
                user=profile.user,
                content_type=ContentType.objects.get_for_model(fast),
                object_id=fast.pk,
                event_type=EventType.objects.get(code="user_joined_fast"),
                timestamp=joined,
                title="Legacy join",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(final_targets)
            period = FastParticipation.objects.get(profile=profile, fast=fast)
            self.assertEqual(period.joined_at, joined)
            self.assertIsNone(period.left_at)
            self.assertFalse(period.ended_at_unknown)
            self.assertEqual(period.fast_original_id, fast.pk)
            self.assertEqual(period.fast_name, fast.name)
            self.assertEqual(period.fast_end_date, end)
            before = list(FastParticipation.objects.values())
            executor = MigrationExecutor(connection)
            executor.migrate([("hub", "0070_fastparticipation")])
            self.assertEqual(list(FastParticipation.objects.values()), before)
            executor = MigrationExecutor(connection)
            executor.migrate(final_targets)
            self.assertEqual(list(FastParticipation.objects.values()), before)
        finally:
            MigrationExecutor(connection).migrate(final_targets)
