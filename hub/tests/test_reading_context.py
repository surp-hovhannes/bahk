from datetime import date
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from hub.models import Church, Day, LLMPrompt, Reading, ReadingContext
from hub.tasks.llm_tasks import generate_reading_context_task
from hub.services.llm_service import OpenAIService, AnthropicService
from tests.fixtures.test_data import TestDataFactory


class ReadingContextTaskTests(TestCase):
    """Tests for the Celery context generation task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = TestDataFactory.create_day(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )

    def test_context_generation_with_gpt(self):
        """Test context generation with GPT model."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        with patch.object(OpenAIService, 'generate_context', return_value="GPT generated context"):
            generate_reading_context_task.run(self.reading.id)
            self.reading.refresh_from_db()
            self.assertEqual(self.reading.contexts.first().text, "GPT generated context")

    def test_context_generation_with_claude(self):
        """Test context generation with Claude model."""
        prompt = LLMPrompt.objects.create(
            model="claude-3-7-sonnet-20250219",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        with patch.object(AnthropicService, 'generate_context', return_value="Claude generated context"):
            generate_reading_context_task.run(self.reading.id)
            self.reading.refresh_from_db()
            self.assertEqual(self.reading.contexts.first().text, "Claude generated context")

    def test_skip_generation_if_exists(self):
        """Test that generation is skipped if context exists and force_regeneration is False."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Create initial context
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Initial context",
            prompt=prompt
        )

        with patch.object(OpenAIService, 'generate_context') as mock_generate:
            generate_reading_context_task.run(self.reading.id)
            mock_generate.assert_not_called()

    def test_force_regeneration(self):
        """Test that force_regeneration creates new context even if one exists."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Create initial context
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Initial context",
            prompt=prompt
        )

        with patch.object(OpenAIService, 'generate_context', return_value="New context"):
            generate_reading_context_task.run(self.reading.id, force_regeneration=True)
            self.reading.refresh_from_db()
            self.assertEqual(self.reading.active_context.text, "New context")


class DailyReadingsAPITests(APITestCase):
    """Tests for the GetDailyReadingsForDate API, including context fields."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = TestDataFactory.create_day(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )
        # Pre-populate context
        ReadingContext.objects.create(
            reading=self.reading,
            text="Existing context",
        )

    def test_daily_readings_api_includes_context_fields(self):
        url = reverse("daily-readings") + f"?date={self.day.date.isoformat()}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("readings", data)
        entry = data["readings"][0]
        self.assertIn("context", entry)
        self.assertEqual(entry["context"], "Existing context")
        self.assertIn("context_thumbs_up", entry)
        self.assertIn("context_thumbs_down", entry)


class FeedbackEndpointTests(APITestCase):
    """Tests for the ReadingContextFeedbackView endpoint."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = TestDataFactory.create_day(date=date.today(), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )
        # Pre-populate context
        self.context = ReadingContext.objects.create(
            reading=self.reading,
            text="Existing context",
        )

    def test_feedback_endpoint_up(self):
        url = reverse("reading-context-feedback", args=[self.reading.id])
        response = self.client.post(url, {"feedback_type": "up"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.context.refresh_from_db()
        self.assertEqual(self.reading.active_context.thumbs_up, 1)

    @patch("hub.views.readings.generate_reading_context_task.delay")
    def test_feedback_endpoint_down_triggers_regeneration(self, mock_delay):
        settings.READING_CONTEXT_REGENERATION_THRESHOLD = 2
        # Start with one down vote
        self.context.thumbs_down = 1
        self.context.save()

        url = reverse("reading-context-feedback", args=[self.reading.id])
        response = self.client.post(url, {"feedback_type": "down"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_down, 2)
        mock_delay.assert_called_once_with(self.reading.id, force_regeneration=True)
