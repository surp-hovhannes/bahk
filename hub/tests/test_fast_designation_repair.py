"""The one-time repair for commemorations the old name regex stamped ``Fast``.

The regex in ``determine_feast_designation_task`` carved out ``Saint`` and never ``St.``, which
is the abbreviation the engine's display text actually uses -- so days plainly naming a saint were
designated generic fasts. Production carries the damage, and because ``designation`` is never
overwritten once set and is the only gate on context generation, those rows never recover on
their own.
"""
from importlib import import_module

from django.test import TestCase

from hub.models import Church, Feast
from hub.services.feast_service import is_commemoration_and_not_a_fast

# Imported by name because a migration module starts with a digit, so it cannot be an identifier.
clear_fast_designation_on_commemorations = import_module(
    "hub.migrations.0068_clear_fast_designation_on_commemorations"
).clear_fast_designation_on_commemorations


class _Apps:
    """Stands in for the migration's historical ``apps`` registry."""

    @staticmethod
    def get_model(app_label, model_name):
        assert (app_label, model_name) == ("hub", "Feast")
        return Feast


class ObservanceMarksTests(TestCase):
    """``is_commemoration_and_not_a_fast`` answers only where the engine is unambiguous."""

    def test_a_commemoration_that_is_not_a_fast(self):
        self.assertTrue(is_commemoration_and_not_a_fast("theodore_the_tyron"))
        self.assertTrue(is_commemoration_and_not_a_fast("last_supper"))
        self.assertTrue(is_commemoration_and_not_a_fast("eve_of_the_resurrection"))

    def test_an_id_carrying_both_marks_is_not_claimed_either_way(self):
        """Six ids are commemoration AND fast. ``Fast`` may be a real answer there, so hands off."""
        self.assertFalse(is_commemoration_and_not_a_fast("twenty_fourth_day_of_great_lent"))
        self.assertFalse(is_commemoration_and_not_a_fast("sixth_sunday_of_great_lent"))

    def test_a_calendar_position_is_not_a_commemoration(self):
        self.assertFalse(is_commemoration_and_not_a_fast("eleventh_sunday_of_the_holy_cross"))

    def test_silence_is_not_evidence(self):
        """An unknown id and a missing id both answer False, never True."""
        self.assertFalse(is_commemoration_and_not_a_fast("no_such_observance_anywhere"))
        self.assertFalse(is_commemoration_and_not_a_fast(None))
        self.assertFalse(is_commemoration_and_not_a_fast(""))


class ClearFastDesignationOnCommemorationsTests(TestCase):
    """What migration 0068 does, driven directly against real rows."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def _feast(self, observance_id, designation, name="x"):
        return Feast.objects.create(
            church=self.church,
            name=f"{name}-{observance_id}",
            observance_id=observance_id,
            designation=designation,
        )

    def test_it_clears_the_stamp_on_a_commemoration(self):
        """The production case: Great Thursday would otherwise keep a blank card forever."""
        feast = self._feast("last_supper", Feast.Designation.FAST)

        clear_fast_designation_on_commemorations(_Apps, None)

        feast.refresh_from_db()
        self.assertIsNone(feast.designation)

    def test_a_cleared_row_becomes_eligible_for_context_again(self):
        """Clearing is the whole repair: NULL is eligible, and the view enqueues from there."""
        from hub.tasks.llm_tasks import is_feast_context_generation_eligible

        feast = self._feast("theodore_the_tyron", Feast.Designation.FAST)
        self.assertFalse(is_feast_context_generation_eligible(feast))

        clear_fast_designation_on_commemorations(_Apps, None)

        feast.refresh_from_db()
        self.assertTrue(is_feast_context_generation_eligible(feast))

    def test_it_leaves_an_id_that_is_both_commemoration_and_fast(self):
        """Mijink carries both marks, so ``Fast`` there may be considered, not a regex artifact."""
        feast = self._feast("twenty_fourth_day_of_great_lent", Feast.Designation.FAST)

        clear_fast_designation_on_commemorations(_Apps, None)

        feast.refresh_from_db()
        self.assertEqual(feast.designation, Feast.Designation.FAST)

    def test_it_leaves_a_calendar_position_alone(self):
        """A position label is not a commemoration; ``Fast`` on it is not this repair's business."""
        feast = self._feast("eleventh_sunday_of_the_holy_cross", Feast.Designation.FAST)

        clear_fast_designation_on_commemorations(_Apps, None)

        feast.refresh_from_db()
        self.assertEqual(feast.designation, Feast.Designation.FAST)

    def test_it_leaves_every_other_designation_alone(self):
        """Only ``Fast`` was regex-assignable. Nothing else is in question."""
        feast = self._feast("last_supper", Feast.Designation.MARTYRS)

        clear_fast_designation_on_commemorations(_Apps, None)

        feast.refresh_from_db()
        self.assertEqual(feast.designation, Feast.Designation.MARTYRS)

    def test_it_leaves_an_unresolved_row_alone(self):
        """A row with no observance id has nothing to check it against."""
        feast = self._feast(None, Feast.Designation.FAST)

        clear_fast_designation_on_commemorations(_Apps, None)

        feast.refresh_from_db()
        self.assertEqual(feast.designation, Feast.Designation.FAST)
