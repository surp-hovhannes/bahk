from abc import ABC, abstractmethod
from typing import Optional
import logging
import anthropic
import openai
from django.conf import settings

from hub.models import LLMPrompt, Reading

logger = logging.getLogger(__name__)

class LLMService(ABC):
    """Base class for LLM services."""
    
    @abstractmethod
    def generate_context(self, reading: Reading, llm_prompt: Optional[LLMPrompt] = None, language_code: str = "en") -> Optional[str]:
        """Generate context for a reading using the LLM service."""
        pass

class AnthropicService(LLMService):
    """Service for Anthropic's Claude API."""
    
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    
    def generate_context(self, reading: Reading, llm_prompt: Optional[LLMPrompt] = None, language_code: str = "en") -> Optional[str]:
        """Generate context using Claude."""
        if not settings.ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY is not configured.")
            return None

        if llm_prompt is None:
            llm_prompt = LLMPrompt.objects.filter(active=True).first()
            if not llm_prompt:
                logger.error("No active LLM prompt found.")
                return None

        try:
            response = self.client.messages.create(
                model=llm_prompt.model,
                system=llm_prompt.prompt,
                messages=[
                    {"role": "user", "content": f"Please provide context for the following passage: {reading.passage_reference}"}
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
        openai.api_key = settings.OPENAI_API_KEY
    
    def generate_context(self, reading: Reading, llm_prompt: Optional[LLMPrompt] = None, language_code: str = "en") -> Optional[str]:
        """Generate context using OpenAI."""
        if not settings.OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not configured.")
            return None

        llm_prompt = llm_prompt or LLMPrompt.objects.get(active=True)
        llm_prompt_combined = f"{llm_prompt.role}\n\n{llm_prompt.prompt}"
        user_prompt_with_passage = f"Contextualize the passage {reading.passage_reference}, by summarizing the passages preceding it."
        
        try:
            response = openai.chat.completions.create(
                model=llm_prompt.model,
                messages=[
                    {
                        "role": "system",
                        "content": llm_prompt_combined,
                    },
                    {"role": "user", "content": user_prompt_with_passage},
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