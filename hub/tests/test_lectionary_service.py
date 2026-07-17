"""Tests for hub.services.lectionary_service.get_daily_readings.

Reading references are computed offline by the ``armenian_lectionary`` engine and parsed into the
component dict shape the persistence layer expects.
"""
from datetime import date, datetime

from django.test import TestCase

from hub.models import Church
from hub.services.lectionary_service import _parse_citation, get_daily_readings

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
