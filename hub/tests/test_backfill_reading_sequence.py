"""Tests for the backfill_reading_sequence management command.

Reading.sequence (migration 0056) is kept correct going forward by
hub.services.lectionary_service.persist_readings(), but rows that existed before that fix (or
before the migration ran) have sequence=NULL and are never touched by it -- the view only calls
persist_readings() when a Day has no readings yet. This command is the one-time backfill for
those rows, so these tests exercise it directly rather than through persist_readings().
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import Church, Day, Reading

_ENGINE_READINGS = [
    {"book": "Isaiah", "book_en": "Isaiah", "start_chapter": 1, "start_verse": 1,
     "end_chapter": 1, "end_verse": 5},
    {"book": "Matthew", "book_en": "Matthew", "start_chapter": 2, "start_verse": 1,
     "end_chapter": 2, "end_verse": 12},
    {"book": "Mark", "book_en": "Mark", "start_chapter": 16, "start_verse": 1,
     "end_chapter": 16, "end_verse": 8},
]

_PATCH_TARGET = "hub.management.commands.backfill_reading_sequence.get_daily_readings"


class BackfillReadingSequenceTests(TestCase):
    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 5, 1), church=self.church)

    def _create_out_of_engine_order(self):
        """Simulate pre-existing rows stored in a different order than the engine returns today."""
        for reading in reversed(_ENGINE_READINGS):
            Reading.objects.create(
                day=self.day,
                book=reading["book"],
                start_chapter=reading["start_chapter"],
                start_verse=reading["start_verse"],
                end_chapter=reading["end_chapter"],
                end_verse=reading["end_verse"],
            )

    def test_assigns_sequence_to_reversed_existing_rows(self):
        self._create_out_of_engine_order()

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            call_command("backfill_reading_sequence", stdout=StringIO())

        served_order = [r.book for r in self.day.readings.all()]
        self.assertEqual(served_order, [r["book"] for r in _ENGINE_READINGS])
        for reading_obj in self.day.readings.all():
            expected_index = [r["book"] for r in _ENGINE_READINGS].index(reading_obj.book)
            self.assertEqual(reading_obj.sequence, expected_index)

    def test_dry_run_makes_no_changes(self):
        self._create_out_of_engine_order()

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            call_command("backfill_reading_sequence", dry_run=True, stdout=StringIO())

        for reading_obj in Reading.objects.filter(day=self.day):
            self.assertIsNone(reading_obj.sequence)
        stored_order = [r.book for r in Reading.objects.filter(day=self.day).order_by("pk")]
        self.assertNotEqual(stored_order, [r["book"] for r in _ENGINE_READINGS])

    def test_second_run_is_a_noop(self):
        self._create_out_of_engine_order()

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            call_command("backfill_reading_sequence", stdout=StringIO())

            out = StringIO()
            call_command("backfill_reading_sequence", stdout=out)

        self.assertIn("rows_updated=0", out.getvalue())
        self.assertIn("days_updated=0", out.getvalue())

    def test_unmatched_row_is_reported_not_created_or_deleted(self):
        # Two rows match the engine list; one ("Ezekiel") does not correspond to anything the
        # engine currently returns for this day (e.g. the engine's citation for it changed).
        Reading.objects.create(
            day=self.day, book="Isaiah", start_chapter=1, start_verse=1,
            end_chapter=1, end_verse=5,
        )
        Reading.objects.create(
            day=self.day, book="Matthew", start_chapter=2, start_verse=1,
            end_chapter=2, end_verse=12,
        )
        stray = Reading.objects.create(
            day=self.day, book="Ezekiel", start_chapter=1, start_verse=1,
            end_chapter=1, end_verse=1,
        )

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            out = StringIO()
            call_command("backfill_reading_sequence", stdout=out, stderr=StringIO())

        self.assertIn("mismatched_days=1", out.getvalue())
        self.assertIn("unmatched_db_rows=1", out.getvalue())
        # Mark (engine index 2) has no matching DB row.
        self.assertIn("unmatched_engine_readings=1", out.getvalue())

        # Never deletes or creates rows.
        self.assertEqual(Reading.objects.filter(day=self.day).count(), 3)
        stray.refresh_from_db()
        self.assertIsNone(stray.sequence)

    def test_strict_raises_on_unmatched_reading(self):
        Reading.objects.create(
            day=self.day, book="Ezekiel", start_chapter=1, start_verse=1,
            end_chapter=1, end_verse=1,
        )

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            with self.assertRaises(CommandError):
                call_command(
                    "backfill_reading_sequence", strict=True,
                    stdout=StringIO(), stderr=StringIO(),
                )

    def test_date_range_scoping_skips_days_outside_range(self):
        self._create_out_of_engine_order()

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            out = StringIO()
            call_command(
                "backfill_reading_sequence",
                start_date="2026-06-01", end_date="2026-06-30",
                stdout=out,
            )

        self.assertIn("days_scanned=0", out.getvalue())
        for reading_obj in Reading.objects.filter(day=self.day):
            self.assertIsNone(reading_obj.sequence)

    def test_church_scoping_only_updates_matching_church(self):
        other_church = Church.objects.create(name="Some Other Church For Backfill Test")
        other_day = Day.objects.create(date=date(2026, 5, 1), church=other_church)
        for reading in reversed(_ENGINE_READINGS):
            Reading.objects.create(
                day=other_day, book=reading["book"],
                start_chapter=reading["start_chapter"], start_verse=reading["start_verse"],
                end_chapter=reading["end_chapter"], end_verse=reading["end_verse"],
            )
        self._create_out_of_engine_order()

        with patch(_PATCH_TARGET, return_value=_ENGINE_READINGS):
            call_command(
                "backfill_reading_sequence", church=self.church.name, stdout=StringIO(),
            )

        for reading_obj in Reading.objects.filter(day=self.day):
            self.assertIsNotNone(reading_obj.sequence)
        for reading_obj in Reading.objects.filter(day=other_day):
            self.assertIsNone(reading_obj.sequence)
