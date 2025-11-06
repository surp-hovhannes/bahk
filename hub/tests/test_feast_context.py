from datetime import date
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from hub.models import Church, Feast, FeastContext, LLMPrompt
from hub.tasks.llm_tasks import generate_feast_context_task
from hub.services.llm_service import OpenAIService, AnthropicService
from tests.fixtures.test_data import TestDataFactory


class FeastContextTaskTests(TestCase):
    """Tests for the Celery feast context generation task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.feast = Feast.objects.create(
            date=date.today(),
            church=self.church,
            name="Christmas",
        )

    def test_context_generation_with_gpt(self):
        """Test context generation with GPT model for all languages."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            model_type="feasts",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Mock generate_context to return different values for each language
        def mock_generate_context(feast, llm_prompt, language_code):
            if language_code == 'en':
                return "GPT generated context in English"
            elif language_code == 'hy':
                return "GPT generated context in Armenian"
            return "GPT generated context"

        with patch.object(OpenAIService, 'generate_context', side_effect=mock_generate_context):
            generate_feast_context_task.run(self.feast.id)
            self.feast.refresh_from_db()
            context = self.feast.contexts.first()
            self.assertEqual(context.text, "GPT generated context in English")
            self.assertEqual(context.text_hy, "GPT generated context in Armenian")
            # Check short_text is also generated
            self.assertIsNotNone(context.short_text)

    def test_context_generation_with_claude(self):
        """Test context generation with Claude model for all languages."""
        prompt = LLMPrompt.objects.create(
            model="claude-3-7-sonnet-20250219",
            model_type="feasts",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Mock generate_context to return different values for each language
        def mock_generate_context(feast, llm_prompt, language_code):
            if language_code == 'en':
                return "Claude generated context in English"
            elif language_code == 'hy':
                return "Claude generated context in Armenian"
            return "Claude generated context"

        with patch.object(AnthropicService, 'generate_context', side_effect=mock_generate_context):
            generate_feast_context_task.run(self.feast.id)
            self.feast.refresh_from_db()
            context = self.feast.contexts.first()
            self.assertEqual(context.text, "Claude generated context in English")
            self.assertEqual(context.text_hy, "Claude generated context in Armenian")
            # Check short_text is also generated
            self.assertIsNotNone(context.short_text)

    def test_skip_generation_if_exists(self):
        """Test that generation is skipped if context exists for all languages and force_regeneration is False."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            model_type="feasts",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Create initial context with all languages
        context = FeastContext.objects.create(
            feast=self.feast,
            text="Initial context in English",
            short_text="Short context in English",
            prompt=prompt
        )
        context.text_hy = "Initial context in Armenian"
        context.short_text_hy = "Short context in Armenian"
        context.save()

        with patch.object(OpenAIService, 'generate_context') as mock_generate:
            generate_feast_context_task.run(self.feast.id)
            mock_generate.assert_not_called()

    def test_force_regeneration(self):
        """Test that force_regeneration creates new context even if one exists."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            model_type="feasts",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

        # Create initial context
        context = FeastContext.objects.create(
            feast=self.feast,
            text="Initial context",
            short_text="Initial short context",
            prompt=prompt
        )

        # Mock generate_context to return different values for each language
        def mock_generate_context(feast, llm_prompt, language_code):
            if language_code == 'en':
                return "New context in English"
            elif language_code == 'hy':
                return "New context in Armenian"
            return "New context"

        with patch.object(OpenAIService, 'generate_context', side_effect=mock_generate_context):
            generate_feast_context_task.run(self.feast.id, force_regeneration=True)
            self.feast.refresh_from_db()
            context = self.feast.active_context
            self.assertEqual(context.text, "New context in English")
            self.assertEqual(context.text_hy, "New context in Armenian")


class FeastByDateAPITests(APITestCase):
    """Tests for the GetFeastByDate API, including context fields."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.feast = Feast.objects.create(
            date=date.today(),
            church=self.church,
            name="Christmas",
        )
        # Create an active LLMPrompt for context generation
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            model_type="feasts",
            role="Test role",
            prompt="Test prompt",
            active=True
        )
        # Pre-populate context with all languages to avoid triggering generation
        context = FeastContext.objects.create(
            feast=self.feast,
            text="Existing context",
            short_text="Existing short context",
        )
        context.text_hy = "Existing context in Armenian"
        context.short_text_hy = "Existing short context in Armenian"
        context.save()

    def test_feast_by_date_api_includes_context_fields(self):
        url = reverse("feast-by-date") + f"?date={self.feast.date.isoformat()}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("feast", data)
        self.assertIsNotNone(data["feast"])
        feast_data = data["feast"]
        self.assertIn("context", feast_data)
        self.assertEqual(feast_data["context"], "Existing context")
        self.assertIn("context_short_text", feast_data)
        self.assertEqual(feast_data["context_short_text"], "Existing short context")
        self.assertIn("context_thumbs_up", feast_data)
        self.assertIn("context_thumbs_down", feast_data)

    def test_feast_by_date_api_no_feast(self):
        """Test API returns None when no feast exists for the date."""
        url = reverse("feast-by-date") + "?date=2025-06-15"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("feast", data)
        self.assertIsNone(data["feast"])

    @patch("hub.views.feasts.scrape_feast")
    @patch("hub.views.feasts.generate_feast_context_task.delay")
    def test_feast_by_date_scrapes_if_not_exists(self, mock_task, mock_scrape):
        """Test that API scrapes feast if it doesn't exist in database."""
        test_date = date(2025, 12, 25)
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": "Սուրբ Ծնունդ",
        }

        url = reverse("feast-by-date") + f"?date={test_date.isoformat()}"
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNotNone(data["feast"])
        self.assertEqual(data["feast"]["name"], "Christmas")
        
        # Verify feast was created in database
        feast = Feast.objects.get(date=test_date, church=self.church)
        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.name_hy, "Սուրբ Ծնունդ")


class FeastFeedbackEndpointTests(APITestCase):
    """Tests for the FeastContextFeedbackView endpoint."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.feast = Feast.objects.create(
            date=date.today(),
            church=self.church,
            name="Christmas",
        )
        # Pre-populate context
        self.context = FeastContext.objects.create(
            feast=self.feast,
            text="Existing context",
            short_text="Existing short context",
        )

    def test_feedback_endpoint_up(self):
        url = reverse("feast-context-feedback", args=[self.feast.id])
        response = self.client.post(url, {"feedback_type": "up"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.context.refresh_from_db()
        self.assertEqual(self.feast.active_context.thumbs_up, 1)

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    def test_feedback_endpoint_down_triggers_regeneration(self, mock_delay):
        settings.FEAST_CONTEXT_REGENERATION_THRESHOLD = 2
        # Start with one down vote
        self.context.thumbs_down = 1
        self.context.save()

        url = reverse("feast-context-feedback", args=[self.feast.id])
        response = self.client.post(url, {"feedback_type": "down"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_down, 2)
        mock_delay.assert_called_once_with(self.feast.id, force_regeneration=True)

    def test_feedback_endpoint_no_context(self):
        """Test feedback endpoint when no context exists."""
        # Delete context
        self.context.delete()
        
        url = reverse("feast-context-feedback", args=[self.feast.id])
        response = self.client.post(url, {"feedback_type": "up"}, format="json")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("error", data["status"])

    def test_feedback_endpoint_invalid_type(self):
        """Test feedback endpoint with invalid feedback type."""
        url = reverse("feast-context-feedback", args=[self.feast.id])
        response = self.client.post(url, {"feedback_type": "invalid"}, format="json")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data["status"])


class FeastTranslationTests(APITestCase):
    """Tests for feast translation handling when scraping feasts."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            model_type="feasts",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

    @patch("hub.views.feasts.scrape_feast")
    @patch("hub.views.feasts.generate_feast_context_task.delay")
    def test_feasts_with_translations_are_saved_correctly(self, mock_task, mock_scrape):
        """Test that feasts with Armenian translations are saved correctly using i18n field."""
        # Mock scraped feast with translations
        mock_scrape.return_value = {
            "name": "Christmas",
            "name_en": "Christmas",
            "name_hy": "Սուրբ Ծնունդ",
        }

        test_date = date(2025, 12, 25)
        url = reverse("feast-by-date") + f"?date={test_date.isoformat()}"
        
        # Make request - this should trigger scraping and saving
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNotNone(data["feast"])
        self.assertEqual(data["feast"]["name"], "Christmas")
        
        # Verify feast was created with translations
        feast = Feast.objects.get(date=test_date, church=self.church)
        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.name_hy, "Սուրբ Ծնունդ")
        
        # Verify translations can be retrieved
        self.assertEqual(feast.name_i18n, "Christmas")  # Default language
        
    def test_feast_translation_field_update(self):
        """Test that updating name_hy translation works with i18n field."""
        feast = Feast.objects.create(
            date=date.today(),
            church=self.church,
            name="Epiphany",
        )
        
        # Verify no Armenian translation initially
        self.assertIsNone(feast.name_hy)
        
        # Set Armenian translation and save with i18n field
        feast.name_hy = "Աստվածահայտնություն"
        feast.save(update_fields=['i18n'])
        
        # Refresh and verify translation was saved
        feast.refresh_from_db()
        self.assertEqual(feast.name_hy, "Աստվածահայտնություն")
        self.assertEqual(feast.name, "Epiphany")
