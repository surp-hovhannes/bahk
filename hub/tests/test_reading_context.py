from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from django.conf import settings

from hub.models import Church, Day, Reading
from hub.tasks.openai_tasks import generate_reading_context_task


class ReadingContextTaskTests(TestCase):
    """Tests for the Celery context generation task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )

    @patch("hub.tasks.openai_tasks.generate_context", return_value="This is a generated context.")
    def test_context_generation_task_success(self, mock_generate):
        # Execute the task synchronously
        generate_reading_context_task.run(self.reading.id)

        # Refresh from DB and assert fields updated
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.context, "This is a generated context.")
        self.assertEqual(self.reading.context_thumbs_up, 0)
        self.assertEqual(self.reading.context_thumbs_down, 0)
        self.assertIsNotNone(self.reading.context_last_generated)
        age = timezone.now() - self.reading.context_last_generated
        self.assertTrue(age.total_seconds() < 5)


class DailyReadingsAPITests(APITestCase):
    """Tests for the GetDailyReadingsForDate API, including context fields."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )
        # Pre-populate context
        self.reading.context = "Existing context"
        self.reading.save()

    def test_daily_readings_api_includes_context_fields(self):
        url = reverse("daily-readings") + f"?date={self.day.date.isoformat()}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("readings", data)
        entry = data["readings"][0]
        self.assertEqual(entry.get("context"), "Existing context")
        self.assertIn("context_thumbs_up", entry)
        self.assertIn("context_thumbs_down", entry)


class FeedbackEndpointTests(APITestCase):
    """Tests for the ReadingContextFeedbackView endpoint."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )

    def test_feedback_endpoint_up(self):
        url = reverse("reading-context-feedback", args=[self.reading.id])
        response = self.client.post(url, {"feedback_type": "up"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.context_thumbs_up, 1)

    @patch("hub.views.readings.generate_reading_context_task.delay")
    def test_feedback_endpoint_down_triggers_regeneration(self, mock_delay):
        settings.READING_CONTEXT_REGENERATION_THRESHOLD = 2
        # Start with one down vote
        self.reading.context_thumbs_down = 1
        self.reading.save()

        url = reverse("reading-context-feedback", args=[self.reading.id])
        response = self.client.post(url, {"feedback_type": "down"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.context_thumbs_down, 2)
        mock_delay.assert_called_once_with(self.reading.id, force_regeneration=True)


class ReadingContextForceTests(TestCase):
    """Tests for skipping or forcing context regeneration in the Celery task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )

    @patch("hub.tasks.openai_tasks.generate_context")
    def test_skip_without_force_regeneration(self, mock_generate):
        # First generation sets context
        mock_generate.return_value = "initial context"
        generate_reading_context_task.run(self.reading.id)
        self.reading.refresh_from_db()
        first_context = self.reading.context
        first_ts = self.reading.context_last_generated

        # Attempt regeneration without force; should skip and not call OpenAI
        mock_generate.return_value = "new context"
        generate_reading_context_task.run(self.reading.id)
        self.reading.refresh_from_db()
        # Context and timestamp remain unchanged
        self.assertEqual(self.reading.context, first_context)
        self.assertEqual(self.reading.context_last_generated, first_ts)
        # ensure OpenAI generate_context was not invoked second time
        self.assertEqual(mock_generate.call_count, 1)

    @patch("hub.tasks.openai_tasks.generate_context")
    def test_force_regeneration_overwrites_context(self, mock_generate):
        # First generation
        mock_generate.return_value = "initial context"
        generate_reading_context_task.run(self.reading.id)
        self.reading.refresh_from_db()
        first_ts = self.reading.context_last_generated

        # Force regeneration
        mock_generate.return_value = "forced context"
        generate_reading_context_task.run(self.reading.id, force_regeneration=True)
        self.reading.refresh_from_db()
        # Context updated and timestamp refreshed
        self.assertEqual(self.reading.context, "forced context")
        self.assertTrue(self.reading.context_last_generated > first_ts) 