import logging

from celery import shared_task
from django.utils import timezone

from hub.models import LLMPrompt, Reading, ReadingContext

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(
    self, reading_id: int, force_regeneration: bool = False, language_code: str = 'en'
):
    """Generate and save AI context for a Reading instance.

    Args:
        reading_id: ID of the Reading to generate context for
        force_regeneration: If True, regenerate even if context exists
        language_code: Language code for the context ('en' or 'hy')
    """
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    # Check if context exists and if the requested language translation is already present
    active_context = reading.active_context
    if active_context is not None and not force_regeneration:
        # Check if the requested language translation exists
        from django.utils import translation
        with translation.override(language_code):
            context_text_i18n = getattr(active_context, 'text_i18n', None)
            # If translation exists and is not empty, skip generation
            if context_text_i18n:
                logger.info(
                    "Reading %s already has context for language %s, skipping generation (force_regeneration=False).",
                    reading_id,
                    language_code
                )
                return

    llm_prompt = LLMPrompt.objects.get(active=True)

    try:
        # Get the appropriate service using the prompt's method
        service = llm_prompt.get_llm_service()
        context_text = service.generate_context(reading, llm_prompt, language_code)

        if context_text:
            # If context exists, update the translation for the requested language
            if active_context is not None:
                from django.utils import translation
                with translation.override(language_code):
                    # Set the translated text field
                    setattr(active_context, f'text_{language_code}', context_text)
                    active_context.save()
                logger.info(
                    "Context translation updated for Reading %s in language %s",
                    reading_id,
                    language_code
                )
            else:
                # Create new context with the translation
                from django.utils import translation
                with translation.override(language_code):
                    context = ReadingContext(
                        reading=reading,
                        text=context_text,
                        prompt=llm_prompt,
                    )
                    # If creating for non-English, set the translation
                    if language_code != 'en':
                        setattr(context, f'text_{language_code}', context_text)
                    context.save()
                logger.info(
                    "Context generated and saved for Reading %s in language %s",
                    reading_id,
                    language_code
                )
        else:
            logger.error("Failed to generate context for Reading %s", reading_id)
            raise self.retry(exc=Exception("Context generation failed"))
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)
