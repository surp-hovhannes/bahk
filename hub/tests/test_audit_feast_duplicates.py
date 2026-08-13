"""Tests for the ``audit_feast_duplicates`` command and its merge rule.

The rule tested here is the one the re-key migration will apply, so these tests are what stop the
report and the migration drifting apart -- the report is only worth running against production if
it predicts what the migration actually does.
"""
import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from hub.management.commands.audit_feast_duplicates import engine_names, survivor
from hub.models import Church, Day, Feast, FeastContext


class SurvivorTests(TestCase):
    """The merge rule: newest active context wins, thumbs sum, oldest row's icon/designation."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def _feast(self, date_obj, name="Feast of the Holy Cross", **kwargs):
        day, _ = Day.objects.get_or_create(date=date_obj, church=self.church)
        return Feast.objects.create(day=day, name=name, **kwargs)

    def _context(self, feast, text, active=True, when=None, up=0, down=0):
        ctx = FeastContext.objects.create(
            feast=feast, text=text, short_text=text[:20], active=active,
            thumbs_up=up, thumbs_down=down,
        )
        if when is not None:
            # auto_now_add ignores an assigned value, so set it after the fact.
            FeastContext.objects.filter(pk=ctx.pk).update(time_of_generation=when)
            ctx.refresh_from_db()
        return ctx

    def test_keeps_the_oldest_row_and_absorbs_the_rest(self):
        first = self._feast(datetime.date(2025, 9, 14))
        second = self._feast(datetime.date(2026, 9, 13))
        merge = survivor([second, first])  # order of input must not matter
        self.assertEqual(merge["keep"], first)
        self.assertEqual(merge["absorbed"], [second])

    def test_newest_active_context_survives(self):
        older = self._feast(datetime.date(2025, 9, 14))
        newer = self._feast(datetime.date(2026, 9, 13))
        now = timezone.now()
        self._context(older, "old text", when=now - datetime.timedelta(days=400))
        keeper = self._context(newer, "new text", when=now)

        merge = survivor([older, newer])
        self.assertEqual(merge["context_kept"], keeper)
        self.assertEqual([c.text for c in merge["contexts_deactivated"]], ["old text"])

    def test_an_inactive_newer_context_does_not_beat_an_active_one(self):
        """FeastContext.save() deactivates siblings per feast, but across merged feasts the
        active flag is the editorial signal -- an inactive row was deliberately retired."""
        older = self._feast(datetime.date(2025, 9, 14))
        newer = self._feast(datetime.date(2026, 9, 13))
        now = timezone.now()
        active = self._context(older, "active", active=True,
                               when=now - datetime.timedelta(days=400))
        self._context(newer, "retired", active=False, when=now)

        self.assertEqual(survivor([older, newer])["context_kept"], active)

    def test_thumbs_are_summed_across_the_group(self):
        a = self._feast(datetime.date(2025, 9, 14))
        b = self._feast(datetime.date(2026, 9, 13))
        self._context(a, "a", up=12, down=1)
        self._context(b, "b", up=3, down=2)

        merge = survivor([a, b])
        self.assertEqual((merge["thumbs_up"], merge["thumbs_down"]), (15, 3))

    def test_first_non_null_icon_and_designation_win_and_conflicts_are_flagged(self):
        a = self._feast(datetime.date(2025, 9, 14))  # no designation
        b = self._feast(datetime.date(2026, 9, 13),
                        designation=Feast.Designation.MARTYRS)
        c = self._feast(datetime.date(2027, 9, 12),
                        designation=Feast.Designation.SUNDAYS_DOMINICAL)

        merge = survivor([a, b, c])
        self.assertEqual(merge["designation"], Feast.Designation.MARTYRS)
        self.assertTrue(merge["designation_conflict"])

    def test_no_conflict_when_the_group_agrees(self):
        a = self._feast(datetime.date(2025, 9, 14), designation=Feast.Designation.MARTYRS)
        b = self._feast(datetime.date(2026, 9, 13), designation=Feast.Designation.MARTYRS)
        merge = survivor([a, b])
        self.assertFalse(merge["designation_conflict"])
        self.assertFalse(merge["icon_conflict"])

    def test_group_without_contexts_survives_cleanly(self):
        a = self._feast(datetime.date(2025, 9, 14))
        merge = survivor([a])
        self.assertIsNone(merge["context_kept"])
        self.assertEqual(merge["contexts_deactivated"], [])
        self.assertEqual((merge["thumbs_up"], merge["thumbs_down"]), (0, 0))


class EngineNamesTests(TestCase):
    """The reachability set the report checks stored names against."""

    def test_covers_a_known_name_and_stays_bounded(self):
        names = engine_names(2026, 2026)
        self.assertIn("Fast day", names)
        # A single year emits far fewer names than it has days; that compression is the whole
        # argument for re-keying, so assert it rather than just non-emptiness.
        self.assertLess(len(names), 365)


class AuditCommandTests(TestCase):
    """The command itself: read-only, and reports the shrink."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        for year in (2025, 2026, 2027):
            day, _ = Day.objects.get_or_create(
                date=datetime.date(year, 9, 14), church=self.church)
            Feast.objects.create(day=day, name="Feast of the Holy Cross")

    def _run(self, **kwargs):
        out = StringIO()
        call_command("audit_feast_duplicates", stdout=out, skip_engine=True, **kwargs)
        return out.getvalue()

    def test_reports_the_collapse(self):
        output = self._run()
        self.assertIn("3 rows -> 1 commemorations", output)

    def test_writes_nothing(self):
        before = list(Feast.objects.values_list("id", "name", "designation", "icon_id"))
        self._run(verbose=True)
        self.assertEqual(
            list(Feast.objects.values_list("id", "name", "designation", "icon_id")), before)

    def test_unknown_church_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("audit_feast_duplicates", church="Nonesuch", stdout=StringIO())

    def test_verbose_lists_the_duplicated_name(self):
        self.assertIn("Feast of the Holy Cross", self._run(verbose=True))
