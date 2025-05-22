import logging
from typing import Optional
import anthropic
from django.conf import settings
from hub.models import LLMPrompt, Reading

logger = logging.getLogger(__name__)

# Initialize Anthropic client
anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

def generate_context(reading: Reading, llm_prompt: Optional[LLMPrompt] = None) -> Optional[str]:
    """
    Generate a contextual introduction for a provided passage using Claude.
    Args:
        reading (Reading): The reading object containing the passage information.
        llm_prompt (Optional[LLMPrompt]): The prompt to use for Claude. If None, uses the active prompt.
    Returns:
        Optional[str]: The generated context, or None if an error occurs.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not configured.")
        return None

    if llm_prompt is None:
        llm_prompt = LLMPrompt.objects.filter(active=True).first()
        if not llm_prompt:
            logger.error("No active LLM prompt found.")
            return None


    try:
        response = anthropic_client.messages.create(
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
    except anthropic.NotFoundError as e:
        logger.error(f"Model not found or not accessible: {model}. Error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating context with Claude: {e}")
        return None 