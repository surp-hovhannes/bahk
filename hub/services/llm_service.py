from abc import ABC, abstractmethod
from typing import Optional
import logging
import json
import os
from difflib import SequenceMatcher
import anthropic
from openai import OpenAI
from django.conf import settings

from hub.models import LLMPrompt, Reading, Feast

logger = logging.getLogger(__name__)

# Constants for language instruction prefixes
_LANGUAGE_INSTRUCTION_PREFIX = "CRITICAL: You MUST respond ONLY in"
_USER_LANGUAGE_PREFIX = "CRITICAL INSTRUCTION: Respond ONLY in"

# Constants for feast reference data matching
MIN_FEAST_NAME_SIMILARITY_THRESHOLD = 0.5  # Minimum similarity score to consider a feast match
DATE_MATCH_CONFIDENCE_BOOST = 0.15  # Boost score by this amount if dates match


def _calculate_similarity(a: str, b: str) -> float:
    """Calculate string similarity ratio between two strings.
    
    Args:
        a: First string (should be lowercased)
        b: Second string (should be lowercased)
        
    Returns:
        Similarity ratio between 0.0 and 1.0
    """
    return SequenceMatcher(None, a, b).ratio()


def _find_feast_in_reference_data(feast) -> Optional[dict]:
    """
    Search the feasts.json file for a matching feast entry.
    
    Primary matching is based on name similarity, with date matching 
    used as a confidence boost (not a requirement).

    Args:
        feast: A Feast model instance

    Returns:
        Dictionary with feast data if found, None otherwise
    """
    # Get the base directory (project root)
    base_dir = settings.BASE_DIR
    feasts_file_path = os.path.join(base_dir, 'data', 'feasts.json')

    if not os.path.exists(feasts_file_path):
        logger.warning(f"Feasts reference file not found at {feasts_file_path}")
        return None

    try:
        with open(feasts_file_path, 'r', encoding='utf-8') as f:
            feasts_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error reading feasts reference file: {e}")
        return None

    # Extract feast date components if available (for confidence boost)
    feast_month = None
    feast_day = None
    has_date = False
    
    if hasattr(feast, 'day') and feast.day and hasattr(feast.day, 'date') and feast.day.date:
        try:
            feast_date = feast.day.date
            feast_month = feast_date.strftime("%B")  # Full month name (e.g., "January")
            feast_day = feast_date.strftime("%d")    # Day with leading zero (e.g., "06")
            has_date = True
            logger.debug(f"Searching for feast reference: {feast.name} on {feast_month} {feast_day}")
        except (AttributeError, ValueError) as e:
            logger.debug(f"Could not extract date from feast {feast.id}: {e}")
    else:
        logger.debug(f"Searching for feast reference: {feast.name} (no date available)")

    # Cache lowercased feast names to avoid repeated .lower() calls
    feast_name_lower = feast.name.lower()
    feast_name_hy_lower = feast.name_hy.lower() if hasattr(feast, 'name_hy') and feast.name_hy else None

    # Search all entries, using name similarity as primary criterion
    best_match = None
    best_score = 0.0
    best_date_match = None

    for entry in feasts_data:
        entry_name = entry.get('name', '')
        entry_name_lower = entry_name.lower()
        
        # Calculate name similarity
        name_score = _calculate_similarity(feast_name_lower, entry_name_lower)

        # Also check Armenian name if available
        if feast_name_hy_lower:
            hy_score = _calculate_similarity(feast_name_hy_lower, entry_name_lower)
            name_score = max(name_score, hy_score)

        # Apply date boost if available and dates match
        final_score = name_score
        date_match = False
        
        if has_date and feast_month and feast_day:
            entry_month = entry.get('month')
            entry_day = entry.get('day')
            if entry_month == feast_month and entry_day == feast_day:
                final_score = min(name_score + DATE_MATCH_CONFIDENCE_BOOST, 1.0)  # Cap at 1.0
                date_match = True

        # Keep track of best match
        if final_score > best_score:
            best_score = final_score
            best_match = entry
            best_date_match = date_match if has_date else None

    # Return the best match if similarity is above threshold
    if best_match and best_score >= MIN_FEAST_NAME_SIMILARITY_THRESHOLD:
        if has_date:
            date_info = f" (date match: {best_date_match})"
        else:
            date_info = " (no date check)"
        logger.info(
            f"Found feast reference: {best_match.get('name', 'N/A')} "
            f"(score: {best_score:.2%}{date_info})"
        )
        return best_match

    # No good match found
    logger.info(f"No feast reference found for: {feast.name} (best score: {best_score:.2%})")
    return None


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


def _parse_feast_context_json(response_text: str) -> Optional[dict]:
    """Parse LLM response for feast context that should contain both text and short_text.
    
    Args:
        response_text: Raw text from LLM, expected to be JSON with 'text' and 'short_text' keys
        
    Returns:
        Dict with 'text' and 'short_text' keys, or None if parsing fails
    """
    import re
    import json

    def _extract_json_object(text_value: str) -> Optional[str]:
        """Extract the first JSON object from a larger string.

        LLMs sometimes wrap JSON in markdown fences or add stray commentary.
        We conservatively take the substring from the first '{' to the last '}'.
        """
        if not isinstance(text_value, str):
            return None
        start = text_value.find("{")
        end = text_value.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text_value[start : end + 1]

    def _escape_control_chars_in_json_strings(json_text: str) -> str:
        """Escape invalid control characters that appear *inside* JSON strings.

        A common LLM failure mode is to emit literal newlines inside quoted values
        (e.g. multi-paragraph `"text": "...\n..."`), which is invalid JSON and raises:
        `Invalid control character at ...`.

        This keeps content intact by converting these characters to JSON escapes.
        """
        if not isinstance(json_text, str):
            return json_text

        out: list[str] = []
        in_string = False
        escaped = False

        for ch in json_text:
            if in_string:
                if escaped:
                    out.append(ch)
                    escaped = False
                    continue

                if ch == "\\":
                    out.append(ch)
                    escaped = True
                    continue

                if ch == '"':
                    out.append(ch)
                    in_string = False
                    continue

                code = ord(ch)
                if code < 0x20:
                    if ch == "\n":
                        out.append("\\n")
                    elif ch == "\r":
                        out.append("\\r")
                    elif ch == "\t":
                        out.append("\\t")
                    else:
                        out.append(f"\\u{code:04x}")
                    continue

                out.append(ch)
            else:
                if ch == '"':
                    out.append(ch)
                    in_string = True
                    continue
                out.append(ch)

        return "".join(out)

    def _safe_json_loads(text_value: str):
        """Parse JSON more defensively for LLM outputs."""
        # Strip outer code fences if present (best-effort).
        text_value = re.sub(r"^\s*```(?:json)?\s*\n", "", text_value)
        text_value = re.sub(r"\n```\s*$", "", text_value)
        text_value = text_value.strip()

        # If the model added chatter, extract the object portion.
        extracted = _extract_json_object(text_value)
        if extracted:
            text_value = extracted

        text_value = _escape_control_chars_in_json_strings(text_value)
        return json.loads(text_value)
    
    def clean_and_extract_text(text_value: str) -> str:
        """Recursively clean and extract plain text from potentially nested JSON/markdown."""
        if not isinstance(text_value, str):
            return text_value
        
        # Remove markdown code blocks
        text_value = re.sub(r'^\s*```(?:json)?\s*\n', '', text_value)
        text_value = re.sub(r'\n```\s*$', '', text_value)
        text_value = text_value.strip()
        
        # Check if the value itself is JSON that needs parsing
        try:
            parsed = _safe_json_loads(text_value)
            if isinstance(parsed, dict):
                # If it has 'text' or 'short_text', extract the appropriate one
                if 'text' in parsed:
                    return clean_and_extract_text(parsed['text'])
                elif 'short_text' in parsed:
                    return clean_and_extract_text(parsed['short_text'])
        except (json.JSONDecodeError, ValueError):
            # Not JSON, return as-is (this is the plain text we want)
            pass
        
        return text_value
    
    # Try to parse as JSON
    try:
        parsed = _safe_json_loads(response_text)
        if isinstance(parsed, dict):
            # Check if we have both required fields
            if 'text' in parsed and 'short_text' in parsed:
                # Recursively clean each field value to extract plain text
                return {
                    'text': clean_and_extract_text(parsed['text']),
                    'short_text': clean_and_extract_text(parsed['short_text'])
                }
            else:
                logger.warning(
                    "Parsed JSON missing required fields. Has: %s, Expected: text, short_text",
                    list(parsed.keys())
                )
                return None
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse feast context JSON: %s", e)
        logger.error("Response text was: %s", response_text[:500])
        return None
    
    return None


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

    @abstractmethod
    def generate_feast_context(self, feast: Feast, llm_prompt: Optional[LLMPrompt] = None, language_code: str = 'en', improvement_instructions: str = None) -> Optional[dict]:
        """Generate context for a feast in the specified language.

        Returns JSON with both 'text' (detailed) and 'short_text' (2-sentence summary).

        Args:
            feast: The Feast instance to generate context for
            llm_prompt: Optional LLMPrompt to use (defaults to active prompt for feasts)
            language_code: Language code for the context ('en', 'hy', or other ISO codes)
            improvement_instructions: Optional instructions to improve the generated content

        Returns:
            Dict with 'text' and 'short_text' keys, or None if generation fails
        """
        pass

    @abstractmethod
    def determine_feast_designation(self, feast: Feast, model_name: str = None) -> Optional[str]:
        """Determine the designation for a feast based on its name.
        
        Args:
            feast: The Feast instance to determine designation for
            model_name: Optional model name to use (defaults to checking active feast prompt or using default)
            
        Returns:
            Designation value (one of the valid choices) or None if determination fails
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
            llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='readings').first()
            if not llm_prompt:
                logger.error("No active LLM prompt found for readings.")
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

    def generate_feast_context(self, feast: Feast, llm_prompt: Optional[LLMPrompt] = None, language_code: str = 'en', improvement_instructions: str = None) -> Optional[dict]:
        """Generate feast context using Claude, returning both text and short_text."""
        if not settings.ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY is not configured.")
            return None

        if llm_prompt is None:
            llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
            if not llm_prompt:
                logger.error("No active LLM prompt found for feasts.")
                return None

        # Include Armenian name if available
        feast_info = f"Feast: {feast.name}"
        if feast.name_hy:
            feast_info += f"\nArmenian name: {feast.name_hy}"

        base_message = (
            f"Provide context for the following feast:\n{feast_info}\n\n"
            "Return your response as JSON with two fields:\n"
            '- "short_text": A 2-sentence summary\n'
            '- "text": A detailed explanation (multiple paragraphs)\n\n'
            "Return ONLY the JSON object, no markdown code blocks.\n"
            r"IMPORTANT: If you need paragraph breaks, encode them as \n in the JSON string values (do not include literal newlines inside quoted strings)."
        )

        # Search for matching feast in reference data
        try:
            feast_reference = _find_feast_in_reference_data(feast)
            if feast_reference:
                logger.debug(f"Found reference data for feast: {feast_reference.get('name', 'N/A')}")
                reference_context = (
                    f"\n\nREFERENCE INFORMATION (use as canonical source):\n"
                    f"Feast Name: {feast_reference.get('name', 'N/A')}\n"
                    f"Description: {feast_reference.get('description', 'N/A')}"
                )
                # Add source URL if available
                if feast_reference.get('source_url'):
                    reference_context += f"\nSource: {feast_reference['source_url']}"

                base_message += reference_context
        except Exception as e:
            logger.error(f"Error during feast reference lookup: {e}", exc_info=True)

        # Add improvement instructions if provided
        if improvement_instructions:
            base_message += (
                f"\n\nIMPROVEMENT INSTRUCTIONS:\n{improvement_instructions}"
            )

        system_prompt, user_message = _build_language_prompts(
            base_message, llm_prompt.prompt, language_code
        )
        user_content = [
            {
                "type": "text",
                "text": user_message
            }
        ]

        try:
            response = self.client.messages.create(
                model=llm_prompt.model,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_content}
                ],
                max_tokens=2000,  # Increased for Armenian text which uses more tokens
                temperature=0.35,
            )
            if response and response.content:
                response_text = response.content[0].text.strip()
                # Parse the JSON response
                return _parse_feast_context_json(response_text)
            logger.error("No content returned from Claude API.")
            return None
        except Exception as e:
            logger.error(f"Error generating feast context with Claude: {e}")
            return None

    def determine_feast_designation(self, feast: Feast, model_name: str = None) -> Optional[str]:
        """Determine the designation for a feast using Claude."""
        if not settings.ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY is not configured.")
            return None

        # Determine model to use
        if model_name is None:
            llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
            if llm_prompt and "claude" in llm_prompt.model:
                model_name = llm_prompt.model
            else:
                model_name = 'claude-sonnet-4-5-20250929'  # Default fallback

        # Hardcoded prompt for designation determination
        designation_options = [
            'Sundays, Dominical Feast Days',
            'St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets',
            'Patriarchs, Vartapets',
            'Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord',
            'Martyrs'
        ]
        
        feast_info = f"Feast name: {feast.name}"
        if feast.name_hy:
            feast_info += f"\nArmenian name: {feast.name_hy}"
        
        system_prompt = (
            "You are a classification expert for Armenian Orthodox Church feasts. "
            "Determine the appropriate designation category for a feast based solely on its name."
        )
        
        user_message = (
            f"{feast_info}\n\n"
            "Based on the feast name above, determine which of the following designation categories it belongs to:\n"
            f"1. {designation_options[0]}\n"
            f"2. {designation_options[1]}\n"
            f"3. {designation_options[2]}\n"
            f"4. {designation_options[3]}\n"
            f"5. {designation_options[4]}\n\n"
            "Return ONLY the exact designation text from the list above, with no additional explanation or formatting."
        )

        try:
            response = self.client.messages.create(
                model=model_name,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ],
                max_tokens=200,
                temperature=0.1,  # Lower temperature for more deterministic classification
            )
            if response and response.content:
                response_text = response.content[0].text.strip()
                # Clean up the response and check if it matches one of the options
                response_text = response_text.strip('"\'')  # Remove quotes if present
                # Check if response matches any of the valid options
                for option in designation_options:
                    if option.lower() == response_text.lower():
                        return option
                # Try partial match
                for option in designation_options:
                    if option.lower() in response_text.lower() or response_text.lower() in option.lower():
                        logger.info(f"Partial match found: '{response_text}' -> '{option}'")
                        return option
                logger.warning(f"Could not match designation response: '{response_text}'")
                return None
            logger.error("No content returned from Claude API for designation.")
            return None
        except Exception as e:
            logger.error(f"Error determining feast designation with Claude: {e}")
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
            llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='readings').first()
            if not llm_prompt:
                logger.error("No active LLM prompt found for readings.")
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

    def generate_feast_context(self, feast: Feast, llm_prompt: Optional[LLMPrompt] = None, language_code: str = 'en', improvement_instructions: str = None) -> Optional[dict]:
        """Generate feast context using OpenAI, returning both text and short_text."""
        if not settings.OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not configured.")
            return None

        if llm_prompt is None:
            llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
            if not llm_prompt:
                logger.error("No active LLM prompt found for feasts.")
                return None

        # Include Armenian name if available
        feast_info = f"Feast: {feast.name}"
        if feast.name_hy:
            feast_info += f"\nArmenian name: {feast.name_hy}"

        base_prompt = (
            f"Provide context for the following feast:\n{feast_info}\n\n"
            "Return your response as JSON with two fields:\n"
            '- "short_text": A 2-sentence summary\n'
            '- "text": A detailed explanation (multiple paragraphs)\n\n'
            "Return ONLY the JSON object, no markdown code blocks.\n"
            r"IMPORTANT: If you need paragraph breaks, encode them as \n in the JSON string values (do not include literal newlines inside quoted strings)."
        )

        # Search for matching feast in reference data
        try:
            feast_reference = _find_feast_in_reference_data(feast)
            if feast_reference:
                logger.debug(f"Found reference data for feast: {feast_reference.get('name', 'N/A')}")
                reference_context = (
                    f"\n\nREFERENCE INFORMATION (use as canonical source):\n"
                    f"Feast Name: {feast_reference.get('name', 'N/A')}\n"
                    f"Description: {feast_reference.get('description', 'N/A')}"
                )
                # Add source URL if available
                if feast_reference.get('source_url'):
                    reference_context += f"\nSource: {feast_reference['source_url']}"

                base_prompt += reference_context
        except Exception as e:
            logger.error(f"Error during feast reference lookup: {e}", exc_info=True)

        # Add improvement instructions if provided
        if improvement_instructions:
            base_prompt += (
                f"\n\nIMPROVEMENT INSTRUCTIONS:\n{improvement_instructions}"
            )

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
                max_tokens=2000,  # Increased for Armenian text which uses more tokens
                temperature=0.35,
                top_p=1,
                store=True
            )
            if response.choices:
                response_text = response.choices[0].message.content.strip()
                # Parse the JSON response
                return _parse_feast_context_json(response_text)
            logger.error("OpenAI response contained no choices")
        except Exception as exc:
            logger.exception("OpenAI API call failed for feast context: %s", exc)
        return None

    def determine_feast_designation(self, feast: Feast, model_name: str = None) -> Optional[str]:
        """Determine the designation for a feast using OpenAI."""
        if not settings.OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not configured.")
            return None

        # Determine model to use
        if model_name is None:
            llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
            if llm_prompt and "gpt" in llm_prompt.model:
                model_name = llm_prompt.model
            else:
                model_name = 'gpt-4o-mini'  # Default fallback

        # Hardcoded prompt for designation determination
        designation_options = [
            'Sundays, Dominical Feast Days',
            'St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets',
            'Patriarchs, Vartapets',
            'Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord',
            'Martyrs'
        ]
        
        feast_info = f"Feast name: {feast.name}"
        if feast.name_hy:
            feast_info += f"\nArmenian name: {feast.name_hy}"
        
        system_prompt = (
            "You are a classification expert for Armenian Orthodox Church feasts. "
            "Determine the appropriate designation category for a feast based solely on its name."
        )
        
        user_message = (
            f"{feast_info}\n\n"
            "Based on the feast name above, determine which of the following designation categories it belongs to:\n"
            f"1. {designation_options[0]}\n"
            f"2. {designation_options[1]}\n"
            f"3. {designation_options[2]}\n"
            f"4. {designation_options[3]}\n"
            f"5. {designation_options[4]}\n\n"
            "Return ONLY the exact designation text from the list above, with no additional explanation or formatting."
        )

        try:
            if not self.client:
                self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=200,
                temperature=0.1,  # Lower temperature for more deterministic classification
                top_p=1,
            )
            if response.choices:
                response_text = response.choices[0].message.content.strip()
                # Clean up the response and check if it matches one of the options
                response_text = response_text.strip('"\'')  # Remove quotes if present
                # Check if response matches any of the valid options
                for option in designation_options:
                    if option.lower() == response_text.lower():
                        return option
                # Try partial match
                for option in designation_options:
                    if option.lower() in response_text.lower() or response_text.lower() in option.lower():
                        logger.info(f"Partial match found: '{response_text}' -> '{option}'")
                        return option
                logger.warning(f"Could not match designation response: '{response_text}'")
                return None
            logger.error("OpenAI response contained no choices for designation.")
            return None
        except Exception as exc:
            logger.exception("OpenAI API call failed for designation: %s", exc)
        return None

def get_llm_service(model_name: str) -> LLMService:
    """Factory function to get the appropriate LLM service based on model name."""
    if "gpt" in model_name:
        return OpenAIService()
    elif "claude" in model_name:
        return AnthropicService()
    else:
        raise ValueError(f"Unsupported model: {model_name}") 
