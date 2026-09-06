"""Regression tests for the released ``armenian_lectionary`` engine.

The ordinary feast-service suite mocks the engine.  These cases exercise the
installed wheel so known regressions cannot pass behind those mocks.

The cases pin *commemorations*, not day names.  The names this file used to pin -- "Fast day",
"Fourth Sunday after Nativity" -- were calendar-position labels, and 2.0.0 retired the bare fast
marker and renamed the Sunday families.  More to the point, a position label commemorates nobody,
so it no longer produces a feast at all: pinning one would pin the absence.
"""
from datetime import date

from django.test import TestCase

from hub.models import Church
from hub.services.feast_service import get_feast_for_date


# (date, [(observance id, English name), ...]) -- the commemorations the engine names that day.
RELEASED_ENGINE_CASES = (
    # One commemoration, and the spelling 1.3.0 corrected from "Saints" to "Sts.".
    (date(2001, 1, 16), [
        ("peter_the_patriarch_blaise",
         "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"),
    ]),
    # Two commemorations on one day -- the case this whole change exists for.
    (date(2001, 1, 18), [
        ("hermit_st_anton", "The Hermit St. Anton"),
        ("hermit_sts_tryphon_barsauma",
         "The Hermit Sts. Tryphon, Barsauma and Onuphrius"),
    ]),
    # A day carrying a position label beside a commemoration: only the latter is served.
    (date(2004, 11, 21), [
        ("presentation_of_the_holy_mother",
         "Presentation of the Holy Mother of God to the Temple"),
        ("eve_of_fast_of_advent", "Eve of the Fast of Advent"),
    ]),
)

# Days whose every component is a calendar position or a bare fast -- nothing to commemorate.
# 5,070 of the engine's 9,861 days are like this, so the empty answer is the common one.
NO_COMMEMORATION_CASES = (
    date(2026, 4, 10),   # Sixth day of Easter
    date(2001, 4, 9),    # Great Monday -- a fast, deliberately not a commemoration
)


class FeastServiceRealEngineTests(TestCase):
    """Check reviewed commemorations against the installed engine wheel."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def test_released_engine_regressions(self):
        for date_obj, expected in RELEASED_ENGINE_CASES:
            with self.subTest(date=date_obj):
                result = get_feast_for_date(date_obj, self.church)

                self.assertIsNotNone(result)
                self.assertEqual(
                    [(entry["observance_id"], entry["name_en"]) for entry in result],
                    expected,
                )
                for entry in result:
                    self.assertEqual(entry["name"], entry["name_en"])
                    self.assertTrue(entry["name_hy"].strip())

    def test_days_that_commemorate_nobody_serve_nothing(self):
        """An empty list, not ``None``: the engine answered, and the answer is "no one"."""
        for date_obj in NO_COMMEMORATION_CASES:
            with self.subTest(date=date_obj):
                result = get_feast_for_date(date_obj, self.church)

                self.assertIsNotNone(result)
                self.assertEqual(result, [])
