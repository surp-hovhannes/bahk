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

    @patch("hub.management.commands.import_readings.scrape_readings")
    def test_import_readings_with_translations(self, mock_scrape):
        """Test that import_readings command correctly saves translations using i18n field."""
        # Mock scraped readings with translations
        mock_scrape.return_value = [
            {
                "book": "Genesis",
                "book_en": "Genesis",
                "book_hy": "Ծննդոց",
                "start_chapter": 1,
                "start_verse": 1,
                "end_chapter": 1,
                "end_verse": 5,
            },
            {
                "book": "Psalms",
                "book_en": "Psalms",
                "book_hy": "Սաղմոսներ",
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

    @patch("hub.management.commands.import_readings.scrape_readings")
    def test_import_readings_without_translations(self, mock_scrape):
        """Test that import_readings works when no translations are provided."""
        # Mock scraped readings without translations
        mock_scrape.return_value = [
            {
                "book": "Matthew",
                "book_en": "Matthew",
                "start_chapter": 5,
                "start_verse": 1,
                "end_chapter": 5,
                "end_verse": 12,
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
        matthew = readings.first()
        self.assertEqual(matthew.book, "Matthew")
        self.assertIsNone(matthew.book_hy)

    @patch("hub.management.commands.import_readings.scrape_readings")
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

        # Mock scrape to return the same reading with translation
        mock_scrape.return_value = [
            {
                "book": "John",
                "book_en": "John",
                "book_hy": "Յովհաննէս",
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

        # Verify translation was added
        reading.refresh_from_db()
        self.assertEqual(reading.book_hy, "Յովհաննէս")

