"""Tests for the get_or_create_feast_for_date utility function."""
from datetime import date
from unittest.mock import patch, Mock

from django.test import TestCase

from hub.models import Church, Day, Feast, Fast
from hub.utils import get_or_create_feast_for_date
from tests.fixtures.test_data import TestDataFactory


class GetOrCreateFeastForDateTests(TestCase):
    """Tests for the get_or_create_feast_for_date utility function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch('hub.utils.scrape_feast')
    def test_create_feast_when_none_exists(self, mock_scrape):
        """Test creating a feast when none exists."""
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": "Սուրբ Ծնունդ",
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify feast was created
        self.assertIsNotNone(feast_obj)
        self.assertTrue(created)
        self.assertEqual(status_dict["status"], "success")
        self.assertEqual(status_dict["action"], "created")
        self.assertEqual(feast_obj.name, "Christmas")
        self.assertEqual(feast_obj.name_hy, "Սուրբ Ծնունդ")
        
        # Verify Day was created
        day = Day.objects.get(date=self.test_date, church=self.church)
        self.assertEqual(day.feasts.first(), feast_obj)

    @patch('hub.utils.scrape_feast')
    def test_skip_when_feast_already_exists(self, mock_scrape):
        """Test skipping when feast already exists and no updates needed."""
        # Create existing feast with complete data
        day = Day.objects.create(date=self.test_date, church=self.church)
        existing_feast = Feast.objects.create(day=day, name="Existing Feast")
        existing_feast.name_hy = "Existing Armenian"  # Has translation already
        existing_feast.save(update_fields=['i18n'])

        # Mock scrape to return None (no feast data found)
        mock_scrape.return_value = None

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify existing feast is returned
        self.assertEqual(feast_obj, existing_feast)
        self.assertFalse(created)
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "feast_already_exists")
        
        # Verify scrape_feast was called (we always scrape now to check for updates)
        mock_scrape.assert_called_once_with(self.test_date, self.church)

    def test_skip_when_fast_associated_with_check_fast_true(self):
        """Test skipping feast lookup when Fast is associated and check_fast=True."""
        # Create a fast
        fast = TestDataFactory.create_fast(church=self.church, name="Lenten Fast")
        
        # Create day with fast associated
        day = Day.objects.create(date=self.test_date, church=self.church, fast=fast)

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify feast lookup was skipped
        self.assertIsNone(feast_obj)
        self.assertFalse(created)
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "fast_associated")
        self.assertEqual(status_dict["fast_name"], "Lenten Fast")

    @patch('hub.utils.scrape_feast')
    def test_continue_when_fast_associated_with_check_fast_false(self, mock_scrape):
        """Test continuing feast lookup when Fast is associated but check_fast=False."""
        # Create a fast
        fast = TestDataFactory.create_fast(church=self.church, name="Lenten Fast")
        
        # Create day with fast associated
        day = Day.objects.create(date=self.test_date, church=self.church, fast=fast)

        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": None,
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=False
        )

        # Verify feast lookup continued despite Fast association
        self.assertIsNotNone(feast_obj)
        self.assertTrue(created)
        self.assertEqual(status_dict["status"], "success")
        mock_scrape.assert_called_once()

    @patch('hub.utils.scrape_feast')
    def test_skip_when_no_feast_data(self, mock_scrape):
        """Test skipping when scrape_feast returns None."""
        mock_scrape.return_value = None

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify no feast was created
        self.assertIsNone(feast_obj)
        self.assertFalse(created)
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "no_feast_data")

    @patch('hub.utils.scrape_feast')
    def test_skip_when_no_feast_name(self, mock_scrape):
        """Test skipping when feast data has no name."""
        mock_scrape.return_value = {
            "name": None,
            "name_en": None,
            "name_hy": None,
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify no feast was created
        self.assertIsNone(feast_obj)
        self.assertFalse(created)
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "no_feast_name")

    @patch('hub.utils.scrape_feast')
    def test_create_feast_with_english_only(self, mock_scrape):
        """Test creating feast with only English name."""
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": None,
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify feast was created
        self.assertIsNotNone(feast_obj)
        self.assertTrue(created)
        self.assertEqual(feast_obj.name, "Christmas")
        self.assertIsNone(feast_obj.name_hy)

    @patch('hub.utils.scrape_feast')
    def test_update_existing_feast_with_missing_translation(self, mock_scrape):
        """Test updating existing feast with missing translation."""
        # Create existing feast without Armenian translation
        day = Day.objects.create(date=self.test_date, church=self.church)
        existing_feast = Feast.objects.create(day=day, name="Christmas")

        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": "Սուրբ Ծնունդ",
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify existing feast was updated with translation
        self.assertEqual(feast_obj, existing_feast)
        self.assertFalse(created)
        self.assertEqual(status_dict["status"], "success")
        self.assertEqual(status_dict["action"], "updated")
        
        # Refresh from DB to get updated translation
        existing_feast.refresh_from_db()
        self.assertEqual(existing_feast.name_hy, "Սուրբ Ծնունդ")

    @patch('hub.utils.scrape_feast')
    def test_does_not_overwrite_existing_translation(self, mock_scrape):
        """Test that existing translation is not overwritten."""
        # Create existing feast with Armenian translation
        day = Day.objects.create(date=self.test_date, church=self.church)
        existing_feast = Feast.objects.create(day=day, name="Christmas")
        existing_feast.name_hy = "Existing Armenian"
        existing_feast.save(update_fields=['i18n'])

        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": "Սուրբ Ծնունդ",  # Different translation
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify existing translation was preserved
        existing_feast.refresh_from_db()
        self.assertEqual(existing_feast.name_hy, "Existing Armenian")

    @patch('hub.utils.scrape_feast')
    def test_creates_day_if_not_exists(self, mock_scrape):
        """Test that Day is created if it doesn't exist."""
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": None,
        }

        # Verify Day doesn't exist
        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify Day was created
        self.assertTrue(Day.objects.filter(date=self.test_date, church=self.church).exists())
        self.assertIsNotNone(feast_obj)

    @patch('hub.utils.scrape_feast')
    def test_uses_existing_day_if_exists(self, mock_scrape):
        """Test that existing Day is used if it exists."""
        # Create existing day
        existing_day = Day.objects.create(date=self.test_date, church=self.church)

        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": None,
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify existing day was used
        self.assertEqual(feast_obj.day, existing_day)
        self.assertEqual(Day.objects.filter(date=self.test_date, church=self.church).count(), 1)

    @patch('hub.utils.scrape_feast')
    def test_handles_fallback_to_name_field(self, mock_scrape):
        """Test handling when name_en is None but name field exists."""
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": None,
            "name_hy": "Սուրբ Ծնունդ",
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Verify feast was created using name field
        self.assertIsNotNone(feast_obj)
        self.assertEqual(feast_obj.name, "Christmas")

