"""Tests for the import_feasts management command."""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from hub.models import Church, Day, Feast


class ImportFeastsCommandTests(TestCase):
    """Tests for the import_feasts management command."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_with_translations(self, mock_scrape):
        """Test that import_feasts command correctly saves translations using i18n field."""
        # Mock scraped feast with translations
        mock_scrape.return_value = {
            "name": "Feast of the Nativity",
            "name_en": "Feast of the Nativity",
            "name_hy": "Ծննդյան տոն",
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
        feast = Feast.objects.get(day__date=date(2025, 12, 25), day__church=self.church)
        self.assertEqual(feast.name, "Feast of the Nativity")
        self.assertEqual(feast.name_hy, "Ծննդյան տոն")

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_without_translations(self, mock_scrape):
        """Test that import_feasts works when no Armenian translation is provided."""
        # Mock scraped feast without Armenian translation
        mock_scrape.return_value = {
            "name": "Easter Sunday",
            "name_en": "Easter Sunday",
        }

        test_date = "2025-04-20"
        end_date = "2025-04-21"
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
        feast = Feast.objects.get(day__date=date(2025, 4, 20), day__church=self.church)
        self.assertEqual(feast.name, "Easter Sunday")
        self.assertIsNone(feast.name_hy)

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_updates_existing(self, mock_scrape):
        """Test that import_feasts updates existing feasts with missing translations."""
        # Create feast without translation
        test_date = date(2025, 1, 6)
        end_date = date(2025, 1, 7)
        day = Day.objects.create(date=test_date, church=self.church)
        feast = Feast.objects.create(
            day=day,
            name="Epiphany",
        )
        
        # Verify no translation initially
        self.assertIsNone(feast.name_hy)

        # Mock scrape to return the same feast with translation
        mock_scrape.return_value = {
            "name": "Epiphany",
            "name_en": "Epiphany",
            "name_hy": "Աստվածայայտնություն",
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
        self.assertEqual(feast.name_hy, "Աստվածայայտնություն")

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_no_feast_found(self, mock_scrape):
        """Test that import_feasts handles dates with no feast gracefully."""
        # Mock scrape returning None (no feast found)
        mock_scrape.return_value = None

        test_date = "2025-03-15"
        end_date = "2025-03-16"
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
        self.assertFalse(
            Feast.objects.filter(day__date=date(2025, 3, 15), day__church=self.church).exists()
        )

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_multiple_dates(self, mock_scrape):
        """Test that import_feasts can handle multiple dates in a range."""
        # Mock scrape to return different feasts for different calls
        mock_scrape.side_effect = [
            {
                "name": "First Feast",
                "name_en": "First Feast",
                "name_hy": "Առաջին տոն",
            },
            None,  # No feast on second day
            {
                "name": "Second Feast",
                "name_en": "Second Feast",
                "name_hy": "Երկրորդ տոն",
            },
        ]

        test_date = "2025-06-01"
        end_date = "2025-06-04"  # Will check June 1, 2, and 3
        out = StringIO()
        
        # Run the command
        call_command(
            "import_feasts",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify first feast was created
        feast1 = Feast.objects.get(day__date=date(2025, 6, 1), day__church=self.church)
        self.assertEqual(feast1.name, "First Feast")
        self.assertEqual(feast1.name_hy, "Առաջին տոն")

        # Verify no feast on second day
        self.assertFalse(
            Feast.objects.filter(day__date=date(2025, 6, 2), day__church=self.church).exists()
        )

        # Verify third feast was created
        feast3 = Feast.objects.get(day__date=date(2025, 6, 3), day__church=self.church)
        self.assertEqual(feast3.name, "Second Feast")
        self.assertEqual(feast3.name_hy, "Երկրորդ տոն")

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_invalid_church(self, mock_scrape):
        """Test that import_feasts handles invalid church name gracefully."""
        test_date = "2025-01-01"
        end_date = "2025-01-02"
        out = StringIO()
        
        # Run the command with non-existent church
        call_command(
            "import_feasts",
            "--church", "NonExistentChurch",
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify scrape_feast was never called
        mock_scrape.assert_not_called()

        # Verify no feast was created
        self.assertEqual(Feast.objects.count(), 0)

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_no_name_in_response(self, mock_scrape):
        """Test that import_feasts skips feasts with no name."""
        # Mock scrape returning feast data with no name
        mock_scrape.return_value = {
            "name_hy": "Տոն",  # Only Armenian name, no English
        }

        test_date = "2025-07-01"
        end_date = "2025-07-02"
        out = StringIO()
        
        # Run the command
        call_command(
            "import_feasts",
            "--church", self.church.name,
            "--start_date", test_date,
            "--end_date", end_date,
            stdout=out
        )

        # Verify no feast was created (no English name to use as default)
        self.assertFalse(
            Feast.objects.filter(day__date=date(2025, 7, 1), day__church=self.church).exists()
        )

    @patch("hub.management.commands.import_feasts.scrape_feast")
    def test_import_feasts_does_not_overwrite_existing_translations(self, mock_scrape):
        """Test that import_feasts doesn't overwrite existing translations."""
        # Create feast with translation
        test_date = date(2025, 8, 15)
        end_date = date(2025, 8, 16)
        day = Day.objects.create(date=test_date, church=self.church)
        feast = Feast.objects.create(
            day=day,
            name="Assumption",
        )
        feast.name_hy = "Վերափոխում"
        feast.save(update_fields=['i18n'])
        
        # Verify translation exists
        self.assertEqual(feast.name_hy, "Վերափոխում")

        # Mock scrape to return different translation
        mock_scrape.return_value = {
            "name": "Assumption",
            "name_en": "Assumption",
            "name_hy": "Դիֆերենտ տրանսլատիօն",  # Different translation
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

        # Verify original translation was NOT overwritten
        feast.refresh_from_db()
        self.assertEqual(feast.name_hy, "Վերափոխում")
