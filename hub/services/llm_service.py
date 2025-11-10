from abc import ABC, abstractmethod
from typing import Optional
import logging
import anthropic
from openai import OpenAI
from django.conf import settings

from hub.models import LLMPrompt, Reading

logger = logging.getLogger(__name__)

# Constants for language instruction prefixes
_LANGUAGE_INSTRUCTION_PREFIX = "CRITICAL: You MUST respond ONLY in"
_USER_LANGUAGE_PREFIX = "CRITICAL INSTRUCTION: Respond ONLY in"


def _build_language_prompts(
    base_prompt: str, llm_prompt_text: str, language_code: str
) -> tuple[str, str]:
    """Build system and user prompts with language instructions.
    
    Args:
        base_prompt: Base user message
        llm_prompt_text: System prompt text
        language_code: Target language code
        
    Returns:
        Tuple of (system_prompt, user_message)
    """
    if language_code == 'hy':
        system_prompt = (
            f"{_LANGUAGE_INSTRUCTION_PREFIX} Armenian language (Հայերեն). "
            "All instructions below apply, but your response must be entirely in Armenian.\n\n"
            f"{llm_prompt_text}"
        )
        user_message = (
            f"{_USER_LANGUAGE_PREFIX} Armenian (Հայերեն).\n\n"
            f"{base_prompt}"
        )
    elif language_code != 'en':
        system_prompt = (
            f"{_LANGUAGE_INSTRUCTION_PREFIX} language code: {language_code}. "
            f"All instructions below apply, but your response must be in that language.\n\n"
            f"{llm_prompt_text}"
        )
        user_message = (
            f"{_USER_LANGUAGE_PREFIX} language code: {language_code}.\n\n"
            f"{base_prompt}"
        )
    else:
        system_prompt = llm_prompt_text
        user_message = base_prompt
    
    return system_prompt, user_message


class LLMService(ABC):
    """Base class for LLM services."""

    @abstractmethod
    def generate_context(self, reading: Reading, llm_prompt: Optional[LLMPrompt] = None, language_code: str = 'en') -> Optional[str]:
        """Generate context for a reading in the specified language.
        
        Modifies the system and user prompts to enforce language output when
        language_code is not 'en'. Currently supports 'en' (English) and 'hy' (Armenian),
        with fallback support for other language codes.
        
        Args:
            reading: The Reading instance to generate context for
            llm_prompt: Optional LLMPrompt to use (defaults to active prompt)
            language_code: Language code for the context ('en', 'hy', or other ISO codes)
            
        Returns:
            Generated context text or None if generation fails
        """
        pass

class AnthropicService(LLMService):
    """Service for Anthropic's Claude API."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def generate_context(self, reading: Reading, llm_prompt: Optional[LLMPrompt] = None, language_code: str = 'en') -> Optional[str]:
        """Generate context using Claude."""
        if not settings.ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY is not configured.")
            return None

        if llm_prompt is None:
            llm_prompt = LLMPrompt.objects.filter(active=True).first()
            if not llm_prompt:
                logger.error("No active LLM prompt found.")
                return None

        base_message = f"Please provide context for the following passage: {reading.passage_reference}"
        system_prompt, user_message = _build_language_prompts(
            base_message, llm_prompt.prompt, language_code
        )

        try:
            response = self.client.messages.create(
                model=llm_prompt.model,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ],
                max_tokens=1000,
                temperature=0.35,
            )
            if response and response.content:
                return response.content[0].text.strip()
            logger.error("No content returned from Claude API.")
            return None
        except Exception as e:
            logger.error(f"Error generating context with Claude: {e}")
            return None

class OpenAIService(LLMService):
    """Service for OpenAI's API."""

    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

    def generate_context(self, reading: Reading, llm_prompt: Optional[LLMPrompt] = None, language_code: str = 'en') -> Optional[str]:
        """Generate context using OpenAI."""
        if not settings.OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not configured.")
            return None

        if llm_prompt is None:
            llm_prompt = LLMPrompt.objects.filter(active=True).first()
            if not llm_prompt:
                logger.error("No active LLM prompt found.")
                return None

        base_prompt = f"Contextualize the passage {reading.passage_reference}, by summarizing the passages preceding it."
        llm_prompt_text = f"{llm_prompt.role}\n\n{llm_prompt.prompt}"
        system_prompt, user_message = _build_language_prompts(
            base_prompt, llm_prompt_text, language_code
        )

        try:
            if not self.client:
                self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = self.client.chat.completions.create(
                model=llm_prompt.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=1000,
                temperature=0.35,
                top_p=1,
                store=True
            )
            if response.choices:
                return response.choices[0].message.content.strip()
            logger.error("OpenAI response contained no choices")
        except Exception as exc:
            logger.exception("OpenAI API call failed: %s", exc)
        return None

def get_llm_service(model_name: str) -> LLMService:
    """Factory function to get the appropriate LLM service based on model name."""
    if "gpt" in model_name:
        return OpenAIService()
    elif "claude" in model_name:
        return AnthropicService()
    else:
        raise ValueError(f"Unsupported model: {model_name}") 