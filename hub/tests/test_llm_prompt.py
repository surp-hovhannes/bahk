from django.test import TestCase
from django.core.exceptions import ValidationError

from hub.models import LLMPrompt
from hub.services.llm_service import OpenAIService, AnthropicService


class LLMPromptTests(TestCase):
    """Tests for the LLMPrompt model."""

    def test_get_llm_service_gpt(self):
        """Test get_llm_service returns OpenAIService for GPT models."""
        prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role",
            prompt="Test prompt"
        )
        service = prompt.get_llm_service()
        self.assertIsInstance(service, OpenAIService)

    def test_get_llm_service_claude(self):
        """Test get_llm_service returns AnthropicService for Claude models."""
        prompt = LLMPrompt.objects.create(
            model="claude-3-7-sonnet-20250219",
            role="Test role",
            prompt="Test prompt"
        )
        service = prompt.get_llm_service()
        self.assertIsInstance(service, AnthropicService)

    def test_get_llm_service_unsupported(self):
        """Test get_llm_service raises ValueError for unsupported models."""
        prompt = LLMPrompt.objects.create(
            model="unsupported-model",
            role="Test role",
            prompt="Test prompt"
        )
        with self.assertRaises(ValueError):
            prompt.get_llm_service()

    def test_only_one_active_prompt(self):
        """Test that only one prompt can be active at a time."""
        prompt1 = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role 1",
            prompt="Test prompt 1",
            active=True
        )
        
        # Creating another active prompt should raise ValidationError
        with self.assertRaises(ValidationError):
            LLMPrompt.objects.create(
                model="gpt-4.1-mini",
                role="Test role 2",
                prompt="Test prompt 2",
                active=True
            )

        # Deactivating first prompt and activating second should work
        prompt2 = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Test role 2",
            prompt="Test prompt 2",
            active=False
        )
        prompt1.active = False
        prompt1.save()
        prompt2.active = True
        prompt2.save()

        self.assertFalse(LLMPrompt.objects.get(pk=prompt1.pk).active)
        self.assertTrue(LLMPrompt.objects.get(pk=prompt2.pk).active) 