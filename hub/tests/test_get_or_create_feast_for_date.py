"""Tests for the get_or_create_feast_for_date utility function."""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase

from hub.models import Church, Day, Feast
from hub.utils import get_or_create_feast_for_date
from tests.fixtures.test_data import TestDataFactory


class GetOrCreateFeastForDateTests(TestCase):
    """Tests for the get_or_create_feast_for_date utility function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch('hub.services.feast_service.get_feast_for_date')
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

        # The feast belongs to the church, not to a day -- and resolving one does not mint a Day.
        self.assertEqual(feast_obj.church, self.church)
        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_skip_when_feast_already_exists(self, mock_scrape):
        """An already-complete commemoration is returned untouched.

        The engine is always consulted now, because the name is what identifies the row -- there
        is no way to know which feast a date maps to without asking. What is skipped is the
        write: the row exists and already carries its translation, so nothing is saved.
        """
        existing_feast = Feast.objects.create(church=self.church, name="Existing Feast")
        existing_feast.name_hy = "Existing Armenian"
        existing_feast.save(update_fields=['i18n'])

        mock_scrape.return_value = {
            "name": "Existing Feast",
            "name_en": "Existing Feast",
            "name_hy": "Existing Armenian",
        }

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feast_obj, existing_feast)
        self.assertFalse(created)
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "feast_already_exists")

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

    @patch('hub.services.feast_service.get_feast_for_date')
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

    @patch('hub.services.feast_service.get_feast_for_date')
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

    @patch('hub.services.feast_service.get_feast_for_date')
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

    @patch('hub.services.feast_service.get_feast_for_date')
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

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_update_existing_feast_with_missing_translation(self, mock_scrape):
        """Test updating existing feast with missing translation."""
        # Create existing feast without Armenian translation
        day = Day.objects.create(date=self.test_date, church=self.church)
        existing_feast = Feast.objects.create(church=day.church, name="Christmas")

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

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_does_not_overwrite_existing_translation(self, mock_scrape):
        """Test that existing translation is not overwritten."""
        # Create existing feast with Armenian translation
        day = Day.objects.create(date=self.test_date, church=self.church)
        existing_feast = Feast.objects.create(church=day.church, name="Christmas")
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

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_creates_day_if_not_exists(self, mock_scrape):
        """Test that Day is created if it doesn't exist."""
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": None,
        }

        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())

        feast_obj, created, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        # Resolving a feast no longer mints calendar rows as a side effect: feasts do not hang
        # off Day any more, so there is nothing to create one for.
        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())
        self.assertIsNotNone(feast_obj)

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_one_row_serves_every_recurrence(self, mock_scrape):
        """The same commemoration on two dates resolves to a single row.

        This replaces a test that asserted the feast reused an existing Day. That is the whole
        point of the re-key: what used to be one row per occurrence is now one row, full stop.
        """
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": None,
        }

        first, created_first, _ = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True)
        second, created_second, status = get_or_create_feast_for_date(
            self.test_date + timedelta(days=365), self.church, check_fast=True)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(status["reason"], "feast_already_exists")
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)

    @patch('hub.services.feast_service.get_feast_for_date')
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

