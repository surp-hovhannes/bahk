"""Tests for the import_readings management command."""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from hub.models import Church, Day, Reading


class ImportReadingsCommandTests(TestCase):
    """Tests for the import_readings management command."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    @patch("hub.management.commands.import_readings.get_daily_readings")
    def test_import_readings_with_translations(self, mock_scrape):
        """Test that import_readings resolves book_hy from usfm_mapping.json at import time.

        Mocked with the real ``get_daily_readings()`` output shape (no ``book_hy`` key --
        the lectionary engine never returns one), so this exercises the actual production
        code path rather than masking it. See PR #461 review.
        """
        mock_scrape.return_value = [
            {
                "book": "Genesis",
                "book_en": "Genesis",
                "start_chapter": 1,
                "start_verse": 1,
                "end_chapter": 1,
                "end_verse": 5,
            },
            {
                "book": "Psalms",
                "book_en": "Psalms",
                "start_chapter": 23,
                "start_verse": 1,
                "end_chapter": 23,
                "end_verse": 6,
            }
        ]

        test_date = "2025-11-07"
        end_date = "2025-11-08"  # daterange doesn't include end_date, so use next day
        out = StringIO()
        
        # Run the command
        call_command(
            "import_readings",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify readings were created with translations
        day = Day.objects.get(date=date(2025, 11, 7), church=self.church)
        readings = day.readings.all()
        self.assertEqual(readings.count(), 2)

        # Check first reading
        genesis = readings.get(book="Genesis")
        self.assertEqual(genesis.book, "Genesis")
        self.assertEqual(genesis.book_hy, "Ծննդոց")

        # Check second reading
        psalms = readings.get(book="Psalms")
        self.assertEqual(psalms.book, "Psalms")
        self.assertEqual(psalms.book_hy, "Սաղմոսներ")

    @patch("hub.management.commands.import_readings.get_daily_readings")
    def test_import_readings_without_translations(self, mock_scrape):
        """Test that import_readings leaves book_hy unset for a book absent from usfm_mapping.json.

        Uses a made-up book name with no ``BOOK_NAME_TO_USFM`` entry, so ``book_hy_for_book``
        legitimately returns ``None`` here -- unlike canonical books such as Matthew, which now
        resolve automatically. (Azariah -- the Daniel-composite deuterocanonical addition --
        used to serve as this example, but now has a real mapping; see test_lectionary_service.)
        """
        mock_scrape.return_value = [
            {
                "book": "Totally Made Up Book",
                "book_en": "Totally Made Up Book",
                "start_chapter": 1,
                "start_verse": 1,
                "end_chapter": 1,
                "end_verse": 20,
            }
        ]

        test_date = "2025-11-08"
        end_date = "2025-11-09"  # daterange doesn't include end_date, so use next day
        out = StringIO()

        # Run the command
        call_command(
            "import_readings",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify reading was created
        day = Day.objects.get(date=date(2025, 11, 8), church=self.church)
        readings = day.readings.all()
        self.assertEqual(readings.count(), 1)

        # Check reading has no Armenian translation
        reading = readings.first()
        self.assertEqual(reading.book, "Totally Made Up Book")
        self.assertIsNone(reading.book_hy)

    @patch("hub.management.commands.import_readings.get_daily_readings")
    def test_import_readings_updates_existing(self, mock_scrape):
        """Test that import_readings updates existing readings with missing translations."""
        # Create reading without translation
        test_date = date(2025, 11, 9)
        end_date = date(2025, 11, 10)  # daterange doesn't include end_date, so use next day
        day = Day.objects.create(date=test_date, church=self.church)
        reading = Reading.objects.create(
            day=day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )
        
        # Verify no translation initially
        self.assertIsNone(reading.book_hy)

        # Mock the lectionary engine's real output shape (no book_hy key)
        mock_scrape.return_value = [
            {
                "book": "John",
                "book_en": "John",
                "start_chapter": 3,
                "start_verse": 16,
                "end_chapter": 3,
                "end_verse": 18,
            }
        ]

        out = StringIO()

        # Run the command
        call_command(
            "import_readings",
            "--church", self.church.name,
            "--start_date", test_date.strftime("%Y-%m-%d"),
            "--end_date", end_date.strftime("%Y-%m-%d"),
            stdout=out
        )

        # Verify translation was resolved from usfm_mapping.json and added
        reading.refresh_from_db()
        self.assertEqual(reading.book_hy, "Աւետարան ըստ Յովհաննէսի")

    @patch("hub.management.commands.import_readings.get_daily_readings")
    def test_import_readings_default_dates_are_computed_at_execution(self, mock_scrape):
        """Test omitted dates use the current date when the command executes."""
        mock_scrape.return_value = []

        class FrozenDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 2, 3)

        out = StringIO()

        with patch("hub.management.commands.import_readings.date", FrozenDate):
            call_command(
                "import_readings",
                "--church", self.church.name,
                stdout=out
            )

        self.assertEqual(mock_scrape.call_count, 10)
        self.assertEqual(mock_scrape.call_args_list[0].args[0].date(), date(2026, 2, 3))
        self.assertEqual(mock_scrape.call_args_list[-1].args[0].date(), date(2026, 2, 12))
