import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from hub.models import LLMPrompt, Reading, ReadingContext

logger = logging.getLogger(__name__)

# Get available languages from settings
AVAILABLE_LANGUAGES = getattr(settings, 'MODELTRANS_AVAILABLE_LANGUAGES', ['en', 'hy'])


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(
    self, reading_id: int, force_regeneration: bool = False, language_code: str = None
):
    """Generate and save AI context for a Reading instance in all available languages.

    Args:
        reading_id: ID of the Reading to generate context for
        force_regeneration: If True, regenerate even if context exists
        language_code: Deprecated - kept for backwards compatibility. All languages are generated.
    """
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    # Check if context exists and has all translations
    active_context = reading.active_context
    if active_context is not None and not force_regeneration:
        # Check if all languages have translations
        all_languages_present = True
        for lang in AVAILABLE_LANGUAGES:
            if lang == 'en':
                context_text = active_context.text
            else:
                context_text = getattr(active_context, f'text_{lang}', None)
            
            if not context_text or not context_text.strip():
                all_languages_present = False
                break
        
        if all_languages_present:
            logger.info(
                "Reading %s already has context for all languages, skipping generation (force_regeneration=False).",
                reading_id
            )
            return

    llm_prompt = LLMPrompt.objects.get(active=True)

    try:
        # Get the appropriate service using the prompt's method
        service = llm_prompt.get_llm_service()
        
        # Generate context for all available languages
        generated_contexts = {}
        for lang in AVAILABLE_LANGUAGES:
            context_text = service.generate_context(reading, llm_prompt, lang)
            if context_text:
                generated_contexts[lang] = context_text
            else:
                logger.warning(
                    "Failed to generate context for Reading %s in language %s",
                    reading_id,
                    lang
                )
        
        if not generated_contexts:
            logger.error("Failed to generate context for Reading %s in any language", reading_id)
            raise self.retry(exc=Exception("Context generation failed for all languages"))
        
        # Create or update context with all translations
        if active_context is not None:
            # Update existing context with missing translations
            for lang, context_text in generated_contexts.items():
                if lang == 'en':
                    # Only update if empty or if force_regeneration
                    if not active_context.text or force_regeneration:
                        active_context.text = context_text
                else:
                    # Only update if empty or if force_regeneration
                    existing_text = getattr(active_context, f'text_{lang}', None)
                    if not existing_text or not existing_text.strip() or force_regeneration:
                        setattr(active_context, f'text_{lang}', context_text)
                        # Ensure the base text field doesn't contain the translation
                        if active_context.text == context_text:
                            active_context.text = ''
            
            active_context.save()
            logger.info(
                "Context translations updated for Reading %s (languages: %s)",
                reading_id,
                ', '.join(generated_contexts.keys())
            )
        else:
            # Create new context with all translations
            # Start with English in the base text field
            english_text = generated_contexts.get('en', '')
            context = ReadingContext(
                reading=reading,
                text=english_text,
                prompt=llm_prompt,
            )
            
            # Set all other language translations
            for lang, context_text in generated_contexts.items():
                if lang != 'en':
                    setattr(context, f'text_{lang}', context_text)
            
            context.save()
            logger.info(
                "Context generated and saved for Reading %s in languages: %s",
                reading_id,
                ', '.join(generated_contexts.keys())
            )
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)
