import logging
from typing import Optional

import openai
from django.conf import settings

from hub.models import LLMPrompt, Reading

logger = logging.getLogger(__name__)


# Ensure the API key is configured
openai.api_key = getattr(settings, "OPENAI_API_KEY", None)


PROMPT_TEMPLATE = (
    "Provide a concise (≤100-word) contextual introduction to {passage}.  "
    "Cover: (1) where this passage sits in the book's narrative and in the overall biblical chronology;  "
    "(2) relevant key events immediately before;  "
    "Offer only neutral biblical background—no interpretation, application, or opinions.  "
    "Ensure nothing conflicts with Oriental Orthodox doctrine."
    "The audience is a layperson who is reading as part of their daily spiritual practice."
)


def generate_context(
    reading: Reading, llm_prompt: Optional[LLMPrompt] = None
) -> Optional[str]:
    """Generate a contextual introduction for the provided passage.

    Returns a string with context or None if the call fails.
    """
    if not openai.api_key:
        logger.error("OPENAI_API_KEY is not configured. Skipping context generation.")
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
