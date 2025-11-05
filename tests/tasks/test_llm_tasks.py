from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase, override_settings
from django.utils import timezone

from hub.models import Reading, ReadingContext, LLMPrompt, Day, Fast, Church
from hub.tasks.llm_tasks import (
    _check_all_translations_present,
    _update_context_translations,
    _create_context_with_translations,
    generate_reading_context_task,
)


class CheckAllTranslationsPresentTests(TestCase):
    """Tests for the _check_all_translations_present function."""

    def setUp(self):
        """Set up test data."""
        # Create a Church and Fast to satisfy foreign key relationships
        self.church = Church.objects.create(name="Test Church")
        self.fast = Fast.objects.create(
            name="Test Fast",
            church=self.church,
            description="Test Description"
        )
        self.day = Day.objects.create(
            date=timezone.now().date(),
            fast=self.fast,
            church=self.church
        )
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18
        )
        self.llm_prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

    def test_all_translations_present(self):
        """Test when all translations are present."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="English text",
            prompt=self.llm_prompt
        )
        context.text_hy = "Armenian text"
        context.save()
        
        result = _check_all_translations_present(context, ['en', 'hy'])
        self.assertTrue(result)

    def test_missing_english_translation(self):
        """Test when English translation is missing."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="",
            prompt=self.llm_prompt
        )
        context.text_hy = "Armenian text"
        context.save()
        
        result = _check_all_translations_present(context, ['en', 'hy'])
        self.assertFalse(result)

    def test_missing_armenian_translation(self):
        """Test when Armenian translation is missing."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="English text",
            prompt=self.llm_prompt
        )
        
        result = _check_all_translations_present(context, ['en', 'hy'])
        self.assertFalse(result)

    def test_whitespace_only_text_counts_as_missing(self):
        """Test that whitespace-only text is treated as missing."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="   ",
            prompt=self.llm_prompt
        )
        context.text_hy = "Armenian text"
        context.save()
        
        result = _check_all_translations_present(context, ['en', 'hy'])
        self.assertFalse(result)


class UpdateContextTranslationsTests(TestCase):
    """Tests for the _update_context_translations function."""

    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")
        self.fast = Fast.objects.create(
            name="Test Fast",
            church=self.church,
            description="Test Description"
        )
        self.day = Day.objects.create(
            date=timezone.now().date(),
            fast=self.fast,
            church=self.church
        )
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18
        )
        self.llm_prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

    def test_update_missing_translations(self):
        """Test updating missing translations."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="English text",
            prompt=self.llm_prompt
        )
        
        generated_contexts = {
            'en': 'English text',
            'hy': 'Armenian text'
        }
        
        _update_context_translations(context, generated_contexts, False)
        
        context.refresh_from_db()
        self.assertEqual(context.text, "English text")
        self.assertEqual(context.text_hy, "Armenian text")

    def test_force_regeneration(self):
        """Test force regeneration overwrites existing translations."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Old English text",
            prompt=self.llm_prompt
        )
        context.text_hy = "Old Armenian text"
        context.save()
        
        generated_contexts = {
            'en': 'New English text',
            'hy': 'New Armenian text'
        }
        
        _update_context_translations(context, generated_contexts, True)
        
        context.refresh_from_db()
        self.assertEqual(context.text, "New English text")
        self.assertEqual(context.text_hy, "New Armenian text")

    def test_preserve_existing_translations_without_force(self):
        """Test that existing translations are preserved when force_regeneration is False."""
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Existing English text",
            prompt=self.llm_prompt
        )
        context.text_hy = "Existing Armenian text"
        context.save()
        
        generated_contexts = {
            'en': 'New English text',
            'hy': 'New Armenian text'
        }
        
        _update_context_translations(context, generated_contexts, False)
        
        context.refresh_from_db()
        # Should keep existing text
        self.assertEqual(context.text, "Existing English text")
        self.assertEqual(context.text_hy, "Existing Armenian text")


class CreateContextWithTranslationsTests(TestCase):
    """Tests for the _create_context_with_translations function."""

    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")
        self.fast = Fast.objects.create(
            name="Test Fast",
            church=self.church,
            description="Test Description"
        )
        self.day = Day.objects.create(
            date=timezone.now().date(),
            fast=self.fast,
            church=self.church
        )
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18
        )
        self.llm_prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

    def test_create_context_with_all_translations(self):
        """Test creating a new context with all translations."""
        generated_contexts = {
            'en': 'English context text',
            'hy': 'Armenian context text'
        }
        
        context = _create_context_with_translations(
            self.reading,
            self.llm_prompt,
            generated_contexts
        )
        
        self.assertIsNotNone(context)
        self.assertEqual(context.reading, self.reading)
        self.assertEqual(context.prompt, self.llm_prompt)
        self.assertEqual(context.text, 'English context text')
        self.assertEqual(context.text_hy, 'Armenian context text')

    def test_create_context_without_armenian(self):
        """Test creating context with only English."""
        generated_contexts = {
            'en': 'English only text'
        }
        
        context = _create_context_with_translations(
            self.reading,
            self.llm_prompt,
            generated_contexts
        )
        
        self.assertIsNotNone(context)
        self.assertEqual(context.text, 'English only text')
        # Armenian text should be None or empty
        self.assertFalse(context.text_hy)


class GenerateReadingContextTaskTests(TestCase):
    """Tests for the generate_reading_context_task Celery task."""

    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")
        self.fast = Fast.objects.create(
            name="Test Fast",
            church=self.church,
            description="Test Description"
        )
        self.day = Day.objects.create(
            date=timezone.now().date(),
            fast=self.fast,
            church=self.church
        )
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18
        )
        self.llm_prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Test role",
            prompt="Test prompt",
            active=True
        )

    def test_reading_not_found(self):
        """Test when reading does not exist."""
        result = generate_reading_context_task(99999)
        # Should return None and log error
        self.assertIsNone(result)

    def test_no_active_prompt(self):
        """Test when no active prompt exists."""
        # Deactivate the prompt created in setUp
        self.llm_prompt.active = False
        self.llm_prompt.save()
        
        # Task should return None and log error when no active prompt exists
        result = generate_reading_context_task(self.reading.id)
        self.assertIsNone(result)

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_skip_when_all_translations_exist(self, mock_generate):
        """Test that task skips when all translations already exist."""
        # Create context with all translations
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Existing English text",
            prompt=self.llm_prompt
        )
        context.text_hy = "Existing Armenian text"
        context.save()
        
        result = generate_reading_context_task(self.reading.id)
        
        # Should not call LLM service
        mock_generate.assert_not_called()
        self.assertIsNone(result)

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_generate_context_success(self, mock_generate):
        """Test successful context generation."""
        # Mock the generate_context to return text for each language
        mock_generate.side_effect = [
            "Generated English context",
            "Generated Armenian context"
        ]
        
        result = generate_reading_context_task(self.reading.id)
        
        # Verify context was created
        self.reading.refresh_from_db()
        context = self.reading.active_context
        self.assertIsNotNone(context)
        self.assertEqual(context.text, "Generated English context")
        self.assertEqual(context.text_hy, "Generated Armenian context")
        
        # Verify generate_context was called twice (for en and hy)
        self.assertEqual(mock_generate.call_count, 2)

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_force_regeneration(self, mock_generate):
        """Test force regeneration overwrites existing context."""
        # Create existing context
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Old English text",
            prompt=self.llm_prompt
        )
        context.text_hy = "Old Armenian text"
        context.save()
        
        # Mock new generation
        mock_generate.side_effect = [
            "New English context",
            "New Armenian context"
        ]
        
        result = generate_reading_context_task(self.reading.id, force_regeneration=True)
        
        # Verify context was updated
        context.refresh_from_db()
        self.assertEqual(context.text, "New English context")
        self.assertEqual(context.text_hy, "New Armenian context")

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_partial_generation_failure(self, mock_generate):
        """Test when generation fails for one language."""
        # Mock successful English, failed Armenian
        mock_generate.side_effect = [
            "Generated English context",
            None  # Failed Armenian generation
        ]
        
        result = generate_reading_context_task(self.reading.id)
        
        # Should still create context with English only
        self.reading.refresh_from_db()
        context = self.reading.active_context
        self.assertIsNotNone(context)
        self.assertEqual(context.text, "Generated English context")

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_complete_generation_failure(self, mock_generate):
        """Test when generation fails for all languages."""
        # Mock failed generation for all languages
        mock_generate.return_value = None
        
        # Mock the retry method
        with patch.object(generate_reading_context_task, 'retry') as mock_retry:
            mock_retry.side_effect = Exception("Retry called")
            
            with self.assertRaises(Exception):
                generate_reading_context_task(self.reading.id)
            
            # Verify retry was called
            mock_retry.assert_called_once()

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_update_existing_context_with_missing_translation(self, mock_generate):
        """Test updating context when one translation is missing."""
        # Create context with only English
        context = ReadingContext.objects.create(
            reading=self.reading,
            text="Existing English text",
            prompt=self.llm_prompt
        )
        
        # Mock generation
        mock_generate.side_effect = [
            "Existing English text",
            "New Armenian context"
        ]
        
        result = generate_reading_context_task(self.reading.id)
        
        # Verify Armenian was added
        context.refresh_from_db()
        self.assertEqual(context.text, "Existing English text")
        self.assertEqual(context.text_hy, "New Armenian context")

    @patch('hub.services.llm_service.AnthropicService.generate_context')
    def test_claude_model_selection(self, mock_generate):
        """Test that Claude model uses AnthropicService."""
        # Update prompt to use Claude
        self.llm_prompt.model = "claude-3-5-sonnet-20241022"
        self.llm_prompt.save()
        
        mock_generate.side_effect = [
            "English from Claude",
            "Armenian from Claude"
        ]
        
        result = generate_reading_context_task(self.reading.id)
        
        # Verify context was created
        self.reading.refresh_from_db()
        context = self.reading.active_context
        self.assertIsNotNone(context)
        
        # Verify generate_context was called (on AnthropicService)
        self.assertEqual(mock_generate.call_count, 2)

    def test_deprecated_language_code_parameter(self):
        """Test that language_code parameter logs a warning."""
        with patch('hub.services.llm_service.OpenAIService.generate_context') as mock_generate:
            mock_generate.side_effect = [
                "English context",
                "Armenian context"
            ]
            
            with self.assertLogs('hub.tasks.llm_tasks', level='WARNING') as cm:
                generate_reading_context_task(self.reading.id, language_code='en')
            
            # Verify warning was logged
            self.assertTrue(
                any('deprecated' in msg.lower() for msg in cm.output)
            )

    @patch('hub.services.llm_service.OpenAIService.generate_context')
    def test_value_error_triggers_retry(self, mock_generate):
        """Test that ValueError from service selection triggers retry."""
        # Update to unsupported model
        self.llm_prompt.model = "unsupported-model"
        self.llm_prompt.save()
        
        with patch.object(generate_reading_context_task, 'retry') as mock_retry:
            mock_retry.side_effect = Exception("Retry called")
            
            with self.assertRaises(Exception):
                generate_reading_context_task(self.reading.id)
            
            # Verify retry was called with ValueError
            mock_retry.assert_called_once()

