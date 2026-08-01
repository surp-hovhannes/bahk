"""Tests for hub.services.lectionary_service.get_daily_readings.

Reading references are computed offline by the ``armenian_lectionary`` engine and parsed into the
component dict shape the persistence layer expects.
"""
from datetime import date, datetime

from django.test import TestCase

from hub.models import Church, Day, Reading
from hub.services.lectionary_service import _parse_citation, get_daily_readings, persist_readings

_FIELDS = {"book", "book_en", "start_chapter", "start_verse", "end_chapter", "end_verse"}


class ParseCitationTests(TestCase):
    """Unit tests for parsing engine citation strings."""

    def test_simple_same_chapter(self):
        self.assertEqual(
            _parse_citation("John 20.1-18"),
            {"book": "John", "book_en": "John", "start_chapter": 20,
             "start_verse": 1, "end_chapter": 20, "end_verse": 18},
        )

    def test_cross_chapter(self):
        p = _parse_citation("Mark 15.42-16.1")
        self.assertEqual((p["start_chapter"], p["start_verse"], p["end_chapter"], p["end_verse"]),
                         (15, 42, 16, 1))

    def test_multiword_pauline_book(self):
        p = _parse_citation("St. Paul's Epistle to the Romans 15.30-16.2")
        self.assertEqual(p["book"], "St. Paul's Epistle to the Romans")
        self.assertEqual((p["start_chapter"], p["end_chapter"], p["end_verse"]), (15, 16, 2))

    def test_single_verse(self):
        p = _parse_citation("1 Corinthians 5.1")
        self.assertEqual(
            (p["book"], p["start_chapter"], p["start_verse"], p["end_chapter"], p["end_verse"]),
            ("1 Corinthians", 5, 1, 5, 1),
        )

    def test_composite_keeps_first_subreference(self):
        p = _parse_citation("Daniel 3.1-23, Azariah 1-68")
        self.assertEqual(p["book"], "Daniel")
        self.assertEqual((p["start_chapter"], p["end_verse"]), (3, 23))

    def test_unparseable_returns_none(self):
        self.assertIsNone(_parse_citation("not a citation at all !!!"))


class GetDailyReadingsTests(TestCase):
    """Integration tests against the real (offline) engine."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def test_easter_2026_readings(self):
        readings = get_daily_readings(date(2026, 4, 5), self.church)
        self.assertTrue(readings, "Expected non-empty readings for Easter 2026")
        first = readings[0]
        self.assertEqual(first["book"], "John")
        self.assertEqual(first["book_en"], "John")
        self.assertEqual(first["start_chapter"], 20)
        self.assertEqual(first["start_verse"], 1)
        self.assertEqual(first["end_verse"], 18)
        for r in readings:
            self.assertEqual(set(r), _FIELDS)
            for k in ("start_chapter", "start_verse", "end_chapter", "end_verse"):
                self.assertIsInstance(r[k], int)

    def test_presentation_eve_malachi_spelling(self):
        # Regression: armenian_lectionary <1.2.2 truncated this book to "Malach" on the
        # Presentation-eve block (Feb 13), which downstream book-name mappings don't recognize,
        # breaking English/offline-Armenian text lookup and Catena URLs. Require the canonical
        # "Malachi" so persistence resolves the book correctly.
        readings = get_daily_readings(date(2026, 2, 13), self.church)
        malachi = [r for r in readings if r["book"] == "Malachi"]
        self.assertTrue(malachi, f"Expected a 'Malachi' reading, got books {[r['book'] for r in readings]}")
        self.assertNotIn("Malach", [r["book"] for r in readings])
        r = malachi[0]
        self.assertEqual(
            (r["book_en"], r["start_chapter"], r["start_verse"], r["end_chapter"], r["end_verse"]),
            ("Malachi", 3, 1, 3, 4),
        )

    def test_accepts_datetime_input(self):
        # import_readings passes datetime objects (not plain dates).
        readings = get_daily_readings(datetime(2026, 4, 5), self.church)
        self.assertTrue(readings)

    def test_year_above_range_returns_empty(self):
        self.assertEqual(get_daily_readings(date(2050, 1, 1), self.church), [])

    def test_year_below_range_returns_empty(self):
        self.assertEqual(get_daily_readings(date(1999, 1, 1), self.church), [])

    def test_unsupported_church_returns_empty(self):
        other = Church.objects.create(name="Some Other Church For Lectionary Test")
        self.assertEqual(get_daily_readings(date(2026, 4, 5), other), [])


class PersistReadingsOrderTests(TestCase):
    """Demonstrates the reading-order bug this module fixes, and that it's fixed.

    Before ``Reading.sequence`` existed, nothing recorded the lectionary's intended reading
    order: ``Reading`` had no ``ordering`` Meta, and callers queried ``day.readings.all()`` with
    no explicit ``order_by``. So the order served by the API was whichever order the rows
    happened to be stored in. That's fine only as long as rows are always created in engine
    order and never touched again -- it breaks the moment they aren't (e.g. rows already existed
    from an earlier import, a retry, or a race between concurrent first-read requests). That's
    exactly what issue #324 ("readings sometimes show up out of order") reports in production.
    """

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 5, 1), church=self.church)
        # The order the lectionary engine actually returns these readings in.
        self.engine_order_readings = [
            {"book": "Isaiah", "book_en": "Isaiah", "start_chapter": 1, "start_verse": 1,
             "end_chapter": 1, "end_verse": 5},
            {"book": "Matthew", "book_en": "Matthew", "start_chapter": 2, "start_verse": 1,
             "end_chapter": 2, "end_verse": 12},
            {"book": "Mark", "book_en": "Mark", "start_chapter": 16, "start_verse": 1,
             "end_chapter": 16, "end_verse": 8},
        ]

    def _create_out_of_engine_order(self):
        """Simulate rows that already exist in a different order than the engine's current list.

        This stands in for the real-world scenario (prior import, retry, race condition) where
        matching ``Reading`` rows are already in the database by the time ``persist_readings`` --
        or, pre-fix, the inline ``get_or_create`` loop it replaced -- runs.
        """
        for reading in reversed(self.engine_order_readings):
            Reading.objects.create(
                day=self.day,
                book=reading["book"],
                start_chapter=reading["start_chapter"],
                start_verse=reading["start_verse"],
                end_chapter=reading["end_chapter"],
                end_verse=reading["end_verse"],
            )

    def test_preexisting_rows_out_of_order_stay_out_of_order_without_sequence(self):
        """Reproduces the bug: with no `sequence` to sort by, stored order is creation order."""
        self._create_out_of_engine_order()

        # No `sequence`, no `ordering` Meta to rely on: `order_by("pk")` is the only ordering
        # signal available, i.e. creation order -- standing in for the pre-fix default queryset.
        stored_order = [r.book for r in Reading.objects.filter(day=self.day).order_by("pk")]
        engine_order = [r["book"] for r in self.engine_order_readings]

        self.assertNotEqual(
            stored_order, engine_order,
            "Setup should reproduce readings stored out of engine order; if this fails, the "
            "reproduction no longer demonstrates the bug and this test (and the sequence fix) "
            "may no longer be needed.",
        )

    def test_persist_readings_assigns_sequence_matching_engine_order(self):
        """The fix: persist_readings() assigns `sequence` from the engine order, so
        `Reading`'s default ordering (`day__date`, `sequence`) serves readings correctly -- even
        when matching rows already existed in a different order.
        """
        self._create_out_of_engine_order()

        persist_readings(self.day, self.engine_order_readings)

        # day.readings.all() relies solely on Reading.Meta.ordering (no explicit order_by), just
        # like hub/views/readings.py and the import_readings management command do.
        served_order = [r.book for r in self.day.readings.all()]
        self.assertEqual(served_order, [r["book"] for r in self.engine_order_readings])

    def test_persist_readings_on_fresh_day_matches_engine_order(self):
        """Sanity check: brand-new readings (the common case) are served in engine order too."""
        persist_readings(self.day, self.engine_order_readings)
        served_order = [r.book for r in self.day.readings.all()]
        self.assertEqual(served_order, [r["book"] for r in self.engine_order_readings])
