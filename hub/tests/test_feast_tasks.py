"""Tests for feast-related Celery tasks."""
from datetime import date, datetime
from unittest.mock import patch, Mock

from django.test import TestCase, override_settings

from hub.models import Church, Day, Feast, Fast
from hub.tasks.feast_tasks import create_feast_date_task
from tests.fixtures.test_data import TestDataFactory


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class CreateFeastDateTaskTests(TestCase):
    """Tests for the create_feast_date_task Celery task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.today = date(2025, 12, 25)

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_create_feast_date_success(self, mock_get_or_create, mock_datetime):
        """Test successful feast date creation."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock feast creation
        mock_feast = Mock(spec=Feast)
        mock_feast.id = 1
        mock_feast.name = "Christmas"
        mock_get_or_create.return_value = (
            mock_feast,
            True,  # created
            {
                "status": "success",
                "action": "created",
                "feast_id": 1,
                "feast_name": "Christmas",
                "date": str(self.today)
            }
        )

        result = create_feast_date_task()

        # Verify result
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["feast_id"], 1)
        mock_get_or_create.assert_called_once_with(
            self.today,
            self.church,
            check_fast=True
        )

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_skip_when_fast_associated(self, mock_get_or_create, mock_datetime):
        """Test skipping when Fast is associated with the day."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock fast association skip
        mock_get_or_create.return_value = (
            None,
            False,
            {
                "status": "skipped",
                "reason": "fast_associated",
                "fast_name": "Lenten Fast",
                "date": str(self.today)
            }
        )

        result = create_feast_date_task()

        # Verify result
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "fast_associated")
        self.assertEqual(result["fast_name"], "Lenten Fast")

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_skip_when_feast_already_exists(self, mock_get_or_create, mock_datetime):
        """Test skipping when feast already exists."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock existing feast
        mock_feast = Mock(spec=Feast)
        mock_get_or_create.return_value = (
            mock_feast,
            False,
            {
                "status": "skipped",
                "reason": "feast_already_exists",
                "date": str(self.today)
            }
        )

        result = create_feast_date_task()

        # Verify result
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "feast_already_exists")

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_skip_when_no_feast_data(self, mock_get_or_create, mock_datetime):
        """Test skipping when no feast data is found."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock no feast data
        mock_get_or_create.return_value = (
            None,
            False,
            {
                "status": "skipped",
                "reason": "no_feast_data",
                "date": str(self.today)
            }
        )

        result = create_feast_date_task()

        # Verify result
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_feast_data")

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_skip_when_no_feast_name(self, mock_get_or_create, mock_datetime):
        """Test skipping when feast has no name."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock no feast name
        mock_get_or_create.return_value = (
            None,
            False,
            {
                "status": "skipped",
                "reason": "no_feast_name",
                "date": str(self.today)
            }
        )

        result = create_feast_date_task()

        # Verify result
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_feast_name")

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_update_existing_feast(self, mock_get_or_create, mock_datetime):
        """Test updating an existing feast."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock feast update
        mock_feast = Mock(spec=Feast)
        mock_feast.id = 1
        mock_feast.name = "Updated Christmas"
        mock_get_or_create.return_value = (
            mock_feast,
            False,  # not created, updated
            {
                "status": "success",
                "action": "updated",
                "feast_id": 1,
                "feast_name": "Updated Christmas",
                "date": str(self.today)
            }
        )

        result = create_feast_date_task()

        # Verify result
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["action"], "updated")

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    @patch('hub.tasks.feast_tasks.sentry_sdk')
    def test_error_handling(self, mock_sentry, mock_get_or_create, mock_datetime):
        """Test error handling in the task."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        # Mock exception
        mock_get_or_create.side_effect = Exception("Database error")

        # Verify exception is raised
        with self.assertRaises(Exception):
            create_feast_date_task()

        # Verify Sentry exception was captured
        mock_sentry.capture_exception.assert_called_once()

    @patch('hub.tasks.feast_tasks.datetime')
    @patch('hub.tasks.feast_tasks.get_or_create_feast_for_date')
    def test_uses_default_church(self, mock_get_or_create, mock_datetime):
        """Test that the task uses the default church."""
        # Mock today's date
        mock_datetime.today.return_value.date.return_value = self.today

        mock_feast = Mock(spec=Feast)
        mock_get_or_create.return_value = (
            mock_feast,
            True,
            {"status": "success", "action": "created", "date": str(self.today)}
        )

        create_feast_date_task()

        # Verify get_or_create_feast_for_date was called with default church
        call_args = mock_get_or_create.call_args
        self.assertEqual(call_args[0][1], self.church)

