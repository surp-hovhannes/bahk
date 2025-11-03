import logging

from celery import shared_task
from django.utils import timezone

from hub.models import LLMPrompt, Reading, ReadingContext

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(
    self, reading_id: int, force_regeneration: bool = False, language_code: str = "en"
):
    """Generate and save AI context for a Reading instance in the requested language."""
    from django.utils.translation import activate
    
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    # Get or create active context
    active_context = reading.active_context
    if active_context is None:
        # Create new context if none exists
        llm_prompt = LLMPrompt.objects.get(active=True)
        active_context = ReadingContext.objects.create(
            reading=reading,
            text="",  # Will be populated below
            prompt=llm_prompt,
        )
    elif not force_regeneration:
        # Check if translation already exists for this language
        activate(language_code)
        translated_text = getattr(active_context, 'text_i18n', None)
        if translated_text and translated_text.strip():
            logger.info(
                "Reading %s already has context in language %s, skipping generation.",
                reading_id, language_code
            )
            return

    llm_prompt = LLMPrompt.objects.get(active=True)
    
    try:
        # Get the appropriate service using the prompt's method
        service = llm_prompt.get_llm_service()
        context_text = service.generate_context(reading, llm_prompt, language_code=language_code)
        
        if context_text:
            # Activate the requested language and save the translation
            activate(language_code)
            active_context.text = context_text
            active_context.save()
            
            logger.info("Context generated and saved for Reading %s in language %s", reading_id, language_code)
        else:
            logger.error("Failed to generate context for Reading %s in language %s", reading_id, language_code)
            raise self.retry(exc=Exception("Context generation failed"))
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)
