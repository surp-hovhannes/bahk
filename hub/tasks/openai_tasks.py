from celery import shared_task
import logging
from django.utils import timezone

from hub.models import Reading
from hub.services.openai_service import generate_context

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(self, reading_id: int, force_regeneration: bool=False):
    """Generate and save AI context for a Reading instance."""
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    # Skip if already generated and not forced
    if reading.context and reading.context_last_generated and not force_regeneration:
        logger.info("Reading %s already has context, skipping generation (force_regeneration=False).", reading_id)
        return

    passage_ref = reading.passage_reference
    if not passage_ref:
        logger.warning("Could not derive passage reference for Reading %s.", reading_id)
        return

    context_text = generate_context(passage_ref)
    if context_text:
        reading.context = context_text
        reading.context_thumbs_up = 0
        reading.context_thumbs_down = 0
        reading.context_last_generated = timezone.now()
        reading.save(update_fields=[
            "context",
            "context_thumbs_up",
            "context_thumbs_down",
            "context_last_generated",
        ])
        logger.info("Context generated and saved for Reading %s", reading_id)
    else:
        logger.error("Failed to generate context for Reading %s", reading_id)
        raise self.retry(exc=Exception("OpenAI generation failed")) 