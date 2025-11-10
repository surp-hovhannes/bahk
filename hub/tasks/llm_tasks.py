import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from hub.models import LLMPrompt, Reading, ReadingContext, Feast, FeastContext
from hub.services.llm_service import get_llm_service

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

    llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='readings').first()
    if not llm_prompt:
        logger.error("No active LLM prompt found for readings.")
        return

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


def _check_all_feast_translations_present(context: FeastContext, languages: list[str]) -> bool:
    """Check if feast context has translations for all languages (both text and short_text)."""
    for lang in languages:
        if lang == 'en':
            text = context.text
            short_text = context.short_text
        else:
            text = getattr(context, f'text_{lang}', None)
            short_text = getattr(context, f'short_text_{lang}', None)
        
        if not text or not text.strip() or not short_text or not short_text.strip():
            return False
    return True


def _update_feast_context_translations(
    context: FeastContext, 
    generated_contexts: dict[str, dict[str, str]], 
    force_regeneration: bool
) -> None:
    """Update existing feast context with missing or regenerated translations.
    
    Args:
        context: FeastContext instance to update
        generated_contexts: Dict mapping language codes to dicts with 'text' and 'short_text' keys
        force_regeneration: If True, overwrite existing translations
    """
    for lang, texts in generated_contexts.items():
        if lang == 'en':
            if not context.text or not context.text.strip() or force_regeneration:
                context.text = texts['text']
            if not context.short_text or not context.short_text.strip() or force_regeneration:
                context.short_text = texts['short_text']
        else:
            existing_text = getattr(context, f'text_{lang}', None)
            if not existing_text or not existing_text.strip() or force_regeneration:
                setattr(context, f'text_{lang}', texts['text'])
            
            existing_short = getattr(context, f'short_text_{lang}', None)
            if not existing_short or not existing_short.strip() or force_regeneration:
                setattr(context, f'short_text_{lang}', texts['short_text'])
    context.save()


def _create_feast_context_with_translations(
    feast: Feast,
    llm_prompt: LLMPrompt,
    generated_contexts: dict[str, dict[str, str]]
) -> FeastContext:
    """Create new feast context with all translations.
    
    Args:
        feast: Feast instance
        llm_prompt: LLMPrompt used for generation
        generated_contexts: Dict mapping language codes to dicts with 'text' and 'short_text' keys
    
    Returns:
        Created FeastContext instance
    """
    english_texts = generated_contexts.get('en', {'text': '', 'short_text': ''})
    context = FeastContext(
        feast=feast,
        text=english_texts['text'],
        short_text=english_texts['short_text'],
        prompt=llm_prompt,
    )
    
    for lang, texts in generated_contexts.items():
        if lang != 'en':
            setattr(context, f'text_{lang}', texts['text'])
            setattr(context, f'short_text_{lang}', texts['short_text'])
    
    context.save()
    return context


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_feast_context_task(
    self, feast_id: int, force_regeneration: bool = False, language_code: str = None
):
    """Generate and save AI context for a Feast instance in all available languages.

    Args:
        feast_id: ID of the Feast to generate context for
        force_regeneration: If True, regenerate even if context exists
        language_code: DEPRECATED - Ignored. All languages are always generated.
    """
    if language_code is not None:
        logger.warning(
            "language_code parameter is deprecated and will be removed in a future version. "
            "All languages are now generated automatically."
        )

    try:
        feast = Feast.objects.get(pk=feast_id)
    except Feast.DoesNotExist:
        logger.error("Feast with id %s not found.", feast_id)
        return

    active_context = feast.active_context
    if active_context and not force_regeneration:
        if _check_all_feast_translations_present(active_context, AVAILABLE_LANGUAGES):
            logger.info(
                "Feast %s already has context for all languages, skipping.",
                feast_id
            )
            return

    llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
    if not llm_prompt:
        logger.error("No active LLM prompt found for feasts.")
        return

    try:
        service = llm_prompt.get_llm_service()
        
        generated_contexts = {}
        for lang in AVAILABLE_LANGUAGES:
            # Generate both text and short_text in a single call
            context_dict = service.generate_feast_context(feast, llm_prompt, lang)
            
            if context_dict and 'text' in context_dict and 'short_text' in context_dict:
                generated_contexts[lang] = context_dict
            else:
                logger.warning(
                    "Failed to generate complete context for Feast %s in language %s",
                    feast_id, lang
                )
        
        if not generated_contexts:
            logger.error("Failed to generate context for Feast %s in any language", feast_id)
            raise self.retry(exc=Exception("Context generation failed for all languages"))
        
        if active_context:
            _update_feast_context_translations(active_context, generated_contexts, force_regeneration)
            logger.info(
                "Context translations updated for Feast %s (languages: %s)",
                feast_id, ', '.join(generated_contexts.keys())
            )
        else:
            _create_feast_context_with_translations(feast, llm_prompt, generated_contexts)
            logger.info(
                "Context generated for Feast %s in languages: %s",
                feast_id, ', '.join(generated_contexts.keys())
            )
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def determine_feast_designation_task(self, feast_id: int):
    """Determine and set the designation for a Feast instance using AI.
    
    Args:
        feast_id: ID of the Feast to determine designation for
    """
    try:
        feast = Feast.objects.get(pk=feast_id)
    except Feast.DoesNotExist:
        logger.error("Feast with id %s not found.", feast_id)
        return

    # Skip if designation is already set (don't overwrite manual assignments)
    if feast.designation:
        logger.info("Feast %s already has designation '%s', skipping.", feast_id, feast.designation)
        return

    # Determine which LLM service to use based on active prompt or default
    model_name = None
    llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
    if llm_prompt:
        model_name = llm_prompt.model
    
    try:
        service = get_llm_service(model_name if model_name else 'claude-sonnet-4-5-20250929')
        designation = service.determine_feast_designation(feast, model_name)
        
        if designation:
            # Verify it's a valid choice
            valid_choices = [choice[0] for choice in Feast.Designation.choices]
            if designation in valid_choices:
                feast.designation = designation
                feast.save(update_fields=['designation'])
                logger.info(
                    "Determined designation '%s' for Feast %s (%s)",
                    designation, feast_id, feast.name
                )
            else:
                logger.warning(
                    "Invalid designation returned: '%s' for Feast %s. Valid choices: %s",
                    designation, feast_id, valid_choices
                )
        else:
            logger.warning("Could not determine designation for Feast %s (%s)", feast_id, feast.name)
    except ValueError as e:
        logger.error(f"Error selecting LLM service for designation: {e}")
        # Don't retry on ValueError, just log the error
    except Exception as e:
        logger.exception(f"Error determining designation for Feast {feast_id}: {e}")
        # Don't retry on general exceptions, just log the error
