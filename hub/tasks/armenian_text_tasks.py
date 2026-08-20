"""Celery tasks for composing Armenian Bible verse text from the offline ``BibleVerse`` corpus.

Provides:
    - fetch_armenian_reading_text_task: compose Armenian text for the passage a Reading
      cites.  Thin Celery wrapper around the "hy" fetcher in
      :mod:`hub.services.reading_text_service`.  Useful for management commands, admin
      actions, or ad-hoc backfills.
"""

import logging

from celery import shared_task

from hub.models import Reading

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_armenian_reading_text_task')
def fetch_armenian_reading_text_task(self, reading_id: int):
    """Compose Armenian text for the passage a Reading cites, from the BibleVerse corpus.

    The text is stored against the passage, so this also satisfies every other reading
    citing it — on any date, for any church.

    Args:
        reading_id: Primary key of a Reading citing the passage to compose.
    """
    try:
        reading = Reading.objects.select_related("day", "day__church").get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    from hub.services.reading_text_service import fetch_all_reading_texts

    if not reading.passage_key:
        logger.warning(
            "Reading %s (%s) has no passage key; add its book to BOOK_NAME_TO_USFM.",
            reading_id, reading.passage_reference,
        )
        return

    try:
        success = fetch_all_reading_texts(reading, langs=["hy"]).get("hy", False)
    except Exception as exc:
        logger.error(
            "Failed to fetch Armenian text for Reading %s: %s",
            reading_id, exc,
        )
        raise self.retry(exc=exc)

    if not success:
        logger.warning(
            "Could not compose Armenian text for Reading %s (%s).",
            reading_id, reading.passage_reference,
        )
