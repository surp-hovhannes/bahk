"""Real-engine (unmocked) contract tests for ``get_feast_for_date``.

``test_feast_service.py`` mocks ``compute_armenian_lectionary`` to test this module's own
branching, which is right for unit tests but means no test ever exercised the wheel that
actually ships.  Every defect the PR #462 review found lived in that gap: placeholder names
on five dates, a wrong name on a sixth, and 54 dates whose name overflows ``Feast.name``.

These tests call the real engine across the whole supported window.  It is offline,
pure-Python and in-process, so sweeping ~9.9k dates costs a couple of seconds.

Two things make the sweep able to catch what the mocked tests could not:

  * it goes through ``full_clean()``, because the test database is SQLite and SQLite does
    not enforce ``max_length`` -- a bare ``save()`` stores an over-long name happily and
    the assertion passes while PostgreSQL would have raised ``DataError``;
  * it asserts on ``name_hy``, because a missing Armenian name is the cheapest signal that
    the engine invented an English name rather than serving one the source uses.
"""

import datetime

from django.test import TestCase

from hub.models import Church, Feast
from hub.services.feast_service import (
    LECTIONARY_MAX_YEAR, LECTIONARY_MIN_YEAR, get_feast_for_date,
)

# Dates the PR #462 review flagged, with the names armenian-lectionary >=1.3 serves.
# 2011 and 2022 are the latest-Easter winters, whose long post-Theophany stretch is where
# the engine previously ran out of validated table and fell back to a placeholder.
REVIEW_REGRESSION_DATES = {
    datetime.date(2011, 2, 4): "Fast day",
    datetime.date(2011, 2, 6): "Fourth Sunday after Nativity",
    datetime.date(2011, 2, 9): "Fast day",
    datetime.date(2011, 2, 11): "Fast day",
    # Carries an eve label: the Fast of the Catechumens opens on Monday 2011-02-14, and 1.3.0
    # appends "Eve of ..." to the day before a fast begins. This fixture predated that.
    datetime.date(2011, 2, 13): "Fifth Sunday after Nativity — Eve of Fast of Catechumens",
    datetime.date(2022, 2, 4): "Fast day",
}

# The two names that overflowed the old 256-char column, and the dates they fall on.
LONG_NAME_DATES = {
    datetime.date(2001, 10, 27): 289,      # The Twelve Holy Doctors of Church: ...
    datetime.date(2001, 12, 6): 257,       # The Holy Fathers of Egypt: ...
}


class FeastServiceRealEngineTests(TestCase):
    """Exercises the shipped wheel, not a mock."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def test_review_flagged_dates_return_real_names(self):
        """The six dates the review found; each must now carry a real name in both languages."""
        for date_obj, expected_en in REVIEW_REGRESSION_DATES.items():
            with self.subTest(date=date_obj):
                result = get_feast_for_date(date_obj, self.church)
                self.assertIsNotNone(result, f"{date_obj} still returns no feast")
                self.assertEqual(result["name_en"], expected_en)
                self.assertIsNotNone(
                    result["name_hy"],
                    f"{date_obj} has no Armenian name; the engine usually lacks one only "
                    "when the English name is not a name the source uses")

    def test_long_names_are_stored_in_full(self):
        """The over-limit names must round-trip intact, not truncated.

        The skipIf that used to guard this is gone: the widening (#471) has landed, so the
        column is wide enough and this runs unconditionally.
        """
        for date_obj, length in LONG_NAME_DATES.items():
            with self.subTest(date=date_obj):
                result = get_feast_for_date(date_obj, self.church)
                self.assertIsNotNone(result)
                self.assertEqual(
                    len(result["name"]), length,
                    "name was clamped; Feast.name is too narrow for the lectionary")

                feast = Feast(church=self.church, name=result["name"])
                feast.full_clean()      # SQLite would not enforce max_length on save()
                feast.save()
                feast.refresh_from_db()
                self.assertEqual(feast.name, result["name"])

    def test_every_supported_date_yields_a_storable_feast(self):
        """Sweep the whole supported window: a real name, an Armenian name, and it fits.

        Covers 2027, which no ground-truth test can reach -- sacredtradition.am publishes
        nothing for it, so the engine's own oracle tests stop at 2026.
        """
        max_length = Feast._meta.get_field("name").max_length
        missing, no_armenian, too_long = [], [], []

        date_obj = datetime.date(LECTIONARY_MIN_YEAR, 1, 1)
        end = datetime.date(LECTIONARY_MAX_YEAR, 12, 31)
        checked = 0
        while date_obj <= end:
            result = get_feast_for_date(date_obj, self.church)
            checked += 1
            if result is None:
                missing.append(date_obj.isoformat())
            else:
                if result["name_hy"] is None:
                    no_armenian.append(date_obj.isoformat())
                if len(result["name"]) > max_length:
                    too_long.append((date_obj.isoformat(), len(result["name"])))
            date_obj += datetime.timedelta(days=1)

        self.assertGreater(checked, 9800, "the supported window should be ~27 years")
        self.assertEqual(
            missing[:10], [],
            f"{len(missing)} supported dates return no feast at all")
        self.assertEqual(
            no_armenian[:10], [],
            f"{len(no_armenian)} supported dates have no Armenian feast name")
        self.assertEqual(
            too_long[:10], [],
            f"{len(too_long)} supported dates exceed the {max_length}-char Feast.name "
            "limit; PostgreSQL raises DataError on these")

    def test_dates_outside_the_window_are_declined(self):
        for date_obj in (datetime.date(LECTIONARY_MIN_YEAR - 1, 6, 1),
                         datetime.date(LECTIONARY_MAX_YEAR + 1, 6, 1)):
            with self.subTest(date=date_obj):
                self.assertIsNone(get_feast_for_date(date_obj, self.church))
