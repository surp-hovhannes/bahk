"""Tests for the import_feasts management command."""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from hub.models import Church, Feast


class ImportFeastsCommandTests(TestCase):
    """Tests for the import_feasts management command."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_with_translations(self, mock_scrape):
        """Test that import_feasts command correctly saves translations using i18n field."""
        # Mock scraped feast with translations
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": "Սուրբ Ծնունդ",
        }

        test_date = "2025-12-25"
        end_date = "2025-12-26"  # daterange doesn't include end_date, so use next day
        out = StringIO()
        
        # Run the command
        call_command(
            "import_feasts",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify feast was created with translations
        feast = Feast.objects.get(date=date(2025, 12, 25), church=self.church)
        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.name_hy, "Սուրբ Ծնունդ")

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_without_translations(self, mock_scrape):
        """Test that import_feasts works when no translations are provided."""
        # Mock scraped feast without translations
        mock_scrape.return_value = {
            "name": "Epiphany",
            "name_en": "Epiphany",
        }

        test_date = "2025-01-06"
        end_date = "2025-01-07"  # daterange doesn't include end_date, so use next day
        out = StringIO()
        
        # Run the command
        call_command(
            "import_feasts",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify feast was created
        feast = Feast.objects.get(date=date(2025, 1, 6), church=self.church)
        self.assertEqual(feast.name, "Epiphany")
        self.assertIsNone(feast.name_hy)

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_updates_existing(self, mock_scrape):
        """Test that import_feasts updates existing feasts with missing translations."""
        # Create feast without translation
        test_date = date(2025, 1, 15)
        end_date = date(2025, 1, 16)  # daterange doesn't include end_date, so use next day
        feast = Feast.objects.create(
            date=test_date,
            church=self.church,
            name="Test Feast",
        )
        
        # Verify no translation initially
        self.assertIsNone(feast.name_hy)

        # Mock scrape to return the same feast with translation
        mock_scrape.return_value = {
            "name": "Test Feast",
            "name_en": "Test Feast",
            "name_hy": "Փորձարկման տոն",
        }

        out = StringIO()
        
        # Run the command
        call_command(
            "import_feasts",
            "--church", self.church.name,
            "--start_date", test_date.strftime("%Y-%m-%d"),
            "--end_date", end_date.strftime("%Y-%m-%d"),
            stdout=out
        )

        # Verify translation was added
        feast.refresh_from_db()
        self.assertEqual(feast.name_hy, "Փորձարկման տոն")

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_no_feast_data(self, mock_scrape):
        """Test that import_feasts handles cases where no feast data is found."""
        # Mock scrape to return None (no feast found)
        mock_scrape.return_value = None

        test_date = "2025-01-20"
        end_date = "2025-01-21"
        out = StringIO()
        
        # Run the command
        call_command(
            "import_feasts",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify no feast was created
        self.assertFalse(Feast.objects.filter(date=date(2025, 1, 20), church=self.church).exists())
