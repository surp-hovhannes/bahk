import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from hub.models import LLMPrompt, Reading, ReadingContext

logger = logging.getLogger(__name__)

AVAILABLE_LANGUAGES = getattr(settings, 'MODELTRANS_AVAILABLE_LANGUAGES', ['en', 'hy'])


def _check_all_translations_present(context: ReadingContext, languages: list[str]) -> bool:
    """Check if context has translations for all languages."""
    for lang in languages:
        if lang == 'en':
            text = context.text
        else:
            text = getattr(context, f'text_{lang}', None)
        
        if not text or not text.strip():
            return False
    return True


def _update_context_translations(
    context: ReadingContext, 
    generated_contexts: dict[str, str], 
    force_regeneration: bool
) -> None:
    """Update existing context with missing or regenerated translations."""
    for lang, context_text in generated_contexts.items():
        if lang == 'en':
            if not context.text or not context.text.strip() or force_regeneration:
                context.text = context_text
        else:
            existing_text = getattr(context, f'text_{lang}', None)
            if not existing_text or not existing_text.strip() or force_regeneration:
                setattr(context, f'text_{lang}', context_text)
    context.save()


def _create_context_with_translations(
    reading: Reading,
    llm_prompt: LLMPrompt,
    generated_contexts: dict[str, str]
) -> ReadingContext:
    """Create new context with all translations."""
    english_text = generated_contexts.get('en', '')
    context = ReadingContext(
        reading=reading,
        text=english_text,
        prompt=llm_prompt,
    )
    
    for lang, context_text in generated_contexts.items():
        if lang != 'en':
            setattr(context, f'text_{lang}', context_text)
    
    context.save()
    return context


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(
    self, reading_id: int, force_regeneration: bool = False, language_code: str = None
):
    """Generate and save AI context for a Reading instance in all available languages.

    Args:
        reading_id: ID of the Reading to generate context for
        force_regeneration: If True, regenerate even if context exists
        language_code: DEPRECATED - Ignored. All languages are always generated.
    """
    if language_code is not None:
        logger.warning(
            "language_code parameter is deprecated and will be removed in a future version. "
            "All languages are now generated automatically."
        )

    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    active_context = reading.active_context
    if active_context and not force_regeneration:
        if _check_all_translations_present(active_context, AVAILABLE_LANGUAGES):
            logger.info(
                "Reading %s already has context for all languages, skipping.",
                reading_id
            )
            return

    llm_prompt = LLMPrompt.objects.get(active=True)

    try:
        service = llm_prompt.get_llm_service()
        
        generated_contexts = {}
        for lang in AVAILABLE_LANGUAGES:
            context_text = service.generate_context(reading, llm_prompt, lang)
            if context_text:
                generated_contexts[lang] = context_text
            else:
                logger.warning(
                    "Failed to generate context for Reading %s in language %s",
                    reading_id, lang
                )
        
        if not generated_contexts:
            logger.error("Failed to generate context for Reading %s in any language", reading_id)
            raise self.retry(exc=Exception("Context generation failed for all languages"))
        
        if active_context:
            _update_context_translations(active_context, generated_contexts, force_regeneration)
            logger.info(
                "Context translations updated for Reading %s (languages: %s)",
                reading_id, ', '.join(generated_contexts.keys())
            )
        else:
            _create_context_with_translations(reading, llm_prompt, generated_contexts)
            logger.info(
                "Context generated for Reading %s in languages: %s",
                reading_id, ', '.join(generated_contexts.keys())
            )
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)
