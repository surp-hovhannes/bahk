import logging
from typing import Optional

import openai
from django.conf import settings

logger = logging.getLogger(__name__)


# Ensure the API key is configured
openai.api_key = getattr(settings, "OPENAI_API_KEY", None)


PROMPT_TEMPLATE = (
    "Provide a concise (≤150-word) contextual introduction to {passage}.  "
    "Cover: (1) where this passage sits in the book's narrative and in the overall biblical chronology;  "
    "(2) key events immediately before and after;  "
    "(3) the book's literary genre and original purpose.  "
    "Offer only neutral historical-literary background—no interpretation, application, or opinions.  "
    "Ensure nothing conflicts with Oriental Orthodox doctrine."
)


def generate_context(passage_reference: str) -> Optional[str]:
    """Generate a contextual introduction for the provided passage.

    Returns a string with context or None if the call fails.
    """
    if not openai.api_key:
        logger.error("OPENAI_API_KEY is not configured. Skipping context generation.")
        return None

    prompt = PROMPT_TEMPLATE.format(passage=passage_reference)
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant providing biblical context with historical and literary background only."
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=250,
            temperature=0.5,
        )
        if response.choices:
            return response.choices[0].message.content.strip()
        logger.error("OpenAI response contained no choices")
    except Exception as exc:
        logger.exception("OpenAI API call failed: %s", exc)
    return None 