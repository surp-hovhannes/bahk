from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church, Day, LLMPrompt, Reading, ReadingContext
from hub.tasks.llm_tasks import generate_reading_context_task
from hub.views.readings import ReadingContextFeedbackView
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
        """Test context generation with GPT model for all languages."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Mock generate_context to return different values for each language
        def mock_generate_context(reading, llm_prompt, language_code):
            if language_code == 'en':
                return "GPT generated context in English"
            elif language_code == 'hy':
                return "GPT generated context in Armenian"
            return "GPT generated context"

        with patch.object(OpenAIService, 'generate_context', side_effect=mock_generate_context):
            generate_reading_context_task.run(self.reading.id)
            self.reading.refresh_from_db()
            context = self.reading.contexts.first()
            self.assertEqual(context.text, "GPT generated context in English")
            self.assertEqual(context.text_hy, "GPT generated context in Armenian")

    def test_context_generation_with_claude(self):
        """Test context generation with Claude model for all languages."""
        prompt = LLMPrompt.objects.create(
            model="claude-3-7-sonnet-20250219",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Mock generate_context to return different values for each language
        def mock_generate_context(reading, llm_prompt, language_code):
            if language_code == 'en':
                return "Claude generated context in English"
            elif language_code == 'hy':
                return "Claude generated context in Armenian"
            return "Claude generated context"

        with patch.object(AnthropicService, 'generate_context', side_effect=mock_generate_context):
            generate_reading_context_task.run(self.reading.id)
            self.reading.refresh_from_db()
            context = self.reading.contexts.first()
            self.assertEqual(context.text, "Claude generated context in English")
            self.assertEqual(context.text_hy, "Claude generated context in Armenian")

    def test_skip_generation_if_exists(self):
        """Test that generation is skipped if context exists for all languages and force_regeneration is False."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Create initial context with all languages
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Initial context in English",
            prompt=prompt
        )
        context.text_hy = "Initial context in Armenian"
        context.save()

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

        # Mock generate_context to return different values for each language
        def mock_generate_context(reading, llm_prompt, language_code):
            if language_code == 'en':
                return "New context in English"
            elif language_code == 'hy':
                return "New context in Armenian"
            return "New context"

        with patch.object(OpenAIService, 'generate_context', side_effect=mock_generate_context):
            generate_reading_context_task.run(self.reading.id, force_regeneration=True)
            self.reading.refresh_from_db()
            context = self.reading.active_context
            self.assertEqual(context.text, "New context in English")
            self.assertEqual(context.text_hy, "New context in Armenian")


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
        # Create an active LLMPrompt for context generation
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )
        # Pre-populate context with all languages to avoid triggering generation
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Existing context",
        )
        context.text_hy = "Existing context in Armenian"
        context.save()

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
        self.url = reverse("reading-context-feedback", args=[self.reading.id])

    def test_mounted_url_resolves_to_reading_feedback_view(self):
        match = resolve(f"/hub/readings/{self.reading.id}/feedback/")

        self.assertEqual(match.func.view_class, ReadingContextFeedbackView)
        self.assertEqual(match.url_name, "reading-context-feedback")

    def test_feedback_endpoint_up(self):
        response = self.client.post(self.url, {"feedback_type": "up"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "success", "regenerate": False})
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_up, 1)
        self.assertEqual(self.context.thumbs_down, 0)

    def test_feedback_endpoint_up_preserves_multiple_votes(self):
        first_response = self.client.post(self.url, {"feedback_type": "up"}, format="json")
        second_response = self.client.post(self.url, {"feedback_type": "up"}, format="json")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_up, 2)

    @patch("hub.views.readings.generate_reading_context_task.delay")
    def test_feedback_endpoint_down_triggers_regeneration(self, mock_delay):
        with self.settings(READING_CONTEXT_REGENERATION_THRESHOLD=1):
            response = self.client.post(
                self.url, {"feedback_type": "down"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "success", "regenerate": True})
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_down, 1)
        mock_delay.assert_called_once_with(self.reading.id, force_regeneration=True)

    def test_feedback_rejects_missing_or_invalid_payload(self):
        missing_response = self.client.post(self.url, {}, format="json")
        invalid_response = self.client.post(
            self.url, {"feedback_type": "sideways"}, format="json"
        )

        self.assertEqual(missing_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            invalid_response.json(),
            {"status": "error", "message": "Invalid feedback type"},
        )
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_up, 0)
        self.assertEqual(self.context.thumbs_down, 0)

    def test_feedback_returns_not_found_for_unknown_reading(self):
        url = reverse("reading-context-feedback", args=[self.reading.id + 999])

        response = self.client.post(url, {"feedback_type": "up"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ReadingTranslationTests(APITestCase):
    """Tests for reading translation handling when scraping readings."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

    @patch("hub.views.readings.get_daily_readings")
    @patch("hub.views.readings.generate_reading_context_task.delay")
    def test_readings_with_translations_are_saved_correctly(self, mock_task, mock_scrape):
        """Test that book_hy is resolved from usfm_mapping.json and saved using the i18n field.

        Mocked with the real ``get_daily_readings()`` output shape (no ``book_hy`` key -- the
        lectionary engine never returns one), so this exercises the actual production code
        path rather than masking it. See PR #461 review.
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
                "book": "Matthew",
                "book_en": "Matthew",
                "start_chapter": 5,
                "start_verse": 1,
                "end_chapter": 5,
                "end_verse": 12,
            }
        ]

        test_date = date(2025, 11, 6)
        url = reverse("daily-readings") + f"?date={test_date.isoformat()}"
        
        # Make request - this should trigger scraping and saving
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["readings"]), 2)
        
        # Verify readings were created with translations
        day = Day.objects.get(date=test_date, church=self.church)
        readings = day.readings.all()
        self.assertEqual(readings.count(), 2)
        
        # Check first reading
        genesis = readings.get(book="Genesis")
        self.assertEqual(genesis.book, "Genesis")
        self.assertEqual(genesis.book_hy, "Ծննդոց")
        
        # Check second reading
        matthew = readings.get(book="Matthew")
        self.assertEqual(matthew.book, "Matthew")
        self.assertEqual(matthew.book_hy, "Աւետարան ըստ Մատթէոսի")
        
        # Verify translations can be retrieved
        self.assertEqual(genesis.book_i18n, "Genesis")  # Default language
        
    def test_reading_translation_field_update(self):
        """Test that updating book_hy translation works with i18n field."""
        day = TestDataFactory.create_day(date=date.today(), church=self.church)
        
        # Create reading without translation
        reading = Reading.objects.create(
            day=day,
            book="Exodus",
            start_chapter=20,
            start_verse=1,
            end_chapter=20,
            end_verse=17,
        )
        
        # Verify no Armenian translation initially
        self.assertIsNone(reading.book_hy)
        
        # Set Armenian translation and save with i18n field
        reading.book_hy = "Ելից"
        reading.save(update_fields=['i18n'])
        
        # Refresh and verify translation was saved
        reading.refresh_from_db()
        self.assertEqual(reading.book_hy, "Ելից")
        self.assertEqual(reading.book, "Exodus")
