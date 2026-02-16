"""Celery tasks for fetching Armenian Bible verse text from sacredtradition.am.

Provides:
    - fetch_armenian_reading_text_task: Fetch Armenian text for a single Reading.
      Thin Celery wrapper around :func:`hub.services.reading_text_service.fetch_armenian_text`.
      Useful for management commands, admin actions, or ad-hoc backfills.
"""

import logging

from celery import shared_task

from hub.models import Reading

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_armenian_reading_text_task')
def fetch_armenian_reading_text_task(self, reading_id: int):
    """Fetch Armenian Bible text for a single Reading from sacredtradition.am.

    Delegates to :func:`hub.services.reading_text_service.fetch_armenian_text`.

    Args:
        reading_id: Primary key of the Reading to fetch Armenian text for.
    """
    try:
        reading = Reading.objects.select_related("day", "day__church").get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    from hub.services.reading_text_service import fetch_armenian_text

    try:
        success = fetch_armenian_text(reading)
    except Exception as exc:
        logger.error(
            "Failed to fetch Armenian text for Reading %s: %s",
            reading_id, exc,
        )
        raise self.retry(exc=exc)

    if not success:
        logger.warning(
            "Could not match Armenian text for Reading %s (%s).",
            reading_id, reading.passage_reference,
        )
