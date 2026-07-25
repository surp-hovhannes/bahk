"""Tests for hub.services.lectionary_service.get_daily_readings.

Reading references are computed offline by the ``armenian_lectionary`` engine and parsed into the
component dict shape the persistence layer expects.
"""
from datetime import date, datetime

from django.test import TestCase

from hub.models import Church
from hub.services.lectionary_service import (
    _parse_citation,
    _parse_reading,
    get_daily_readings,
)

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

    def test_composite_string_is_not_a_single_citation(self):
        # _parse_citation handles one reference; a comma-joined composite is not parseable
        # here -- it is split upstream by _parse_reading.
        self.assertIsNone(_parse_citation("Daniel 3.1-23, Azariah 1-68"))

    def test_strips_trailing_period_on_book(self):
        # The engine emits a stray trailing period on some heads (e.g. "Azariah. 1-68").
        p = _parse_citation("Azariah. 1-68")
        self.assertEqual(
            (p["book"], p["start_chapter"], p["start_verse"], p["end_chapter"], p["end_verse"]),
            ("Azariah", 1, 1, 1, 68),
        )

    def test_unparseable_returns_none(self):
        self.assertIsNone(_parse_citation("not a citation at all !!!"))


class ParseReadingTests(TestCase):
    """Unit tests for splitting a citation into its persisted sub-references."""

    def test_single_reference_returns_one(self):
        self.assertEqual([r["book"] for r in _parse_reading("John 20.1-18")], ["John"])

    def test_composite_splits_into_all_subreferences(self):
        # The only scripture composite in the engine: both halves must be persisted, not just
        # the first (Daniel), and the Azariah head's trailing period is stripped.
        parts = _parse_reading("Daniel 3.1-23, Azariah. 1-68")
        self.assertEqual(
            [(p["book"], p["start_chapter"], p["start_verse"], p["end_chapter"], p["end_verse"])
             for p in parts],
            [("Daniel", 3, 1, 3, 23), ("Azariah", 1, 1, 1, 68)],
        )

    def test_drops_unparseable_subreferences(self):
        parts = _parse_reading("John 20.1-18, !!!garbage!!!")
        self.assertEqual([p["book"] for p in parts], ["John"])


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

    def test_composite_day_persists_both_daniel_and_azariah(self):
        # Great Saturday 2026 includes the composite "Daniel 3.1-23, Azariah. 1-68"; both halves
        # must appear as separate readings (Azariah was previously dropped).
        readings = get_daily_readings(date(2026, 4, 4), self.church)
        daniel = [r for r in readings if r["book"] == "Daniel"]
        azariah = [r for r in readings if r["book"] == "Azariah"]
        self.assertTrue(daniel, "Expected a Daniel reading on Great Saturday")
        self.assertTrue(azariah, "Expected the Azariah reading to be persisted, not dropped")
        self.assertEqual(
            (daniel[0]["start_chapter"], daniel[0]["end_verse"]), (3, 23))
        self.assertEqual(
            (azariah[0]["start_chapter"], azariah[0]["start_verse"],
             azariah[0]["end_chapter"], azariah[0]["end_verse"]),
            (1, 1, 1, 68),
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
