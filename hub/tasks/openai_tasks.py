import logging

from celery import shared_task
from django.utils import timezone

from hub.models import LLMPrompt, Reading, ReadingContext
from hub.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(
    self, reading_id: int, force_regeneration: bool = False
):
    """Generate and save AI context for a Reading instance."""
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    # Skip if already generated and not forced
    if reading.active_context is not None and not force_regeneration:
        logger.info(
            "Reading %s already has context, skipping generation (force_regeneration=False).",
            reading_id,
        )
        return

    llm_prompt = LLMPrompt.objects.get(active=True)
    
    try:
        # Get the appropriate service for the model
        service = get_llm_service(llm_prompt.model)
        context_text = service.generate_context(reading, llm_prompt)
        
        if context_text:
            ReadingContext.objects.create(
                reading=reading,
                text=context_text,
                prompt=llm_prompt,
            )
            logger.info("Context generated and saved for Reading %s", reading_id)
        else:
            logger.error("Failed to generate context for Reading %s", reading_id)
            raise self.retry(exc=Exception("Context generation failed"))
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)
