"""Celery tasks for fetching Armenian Bible verse text from sacredtradition.am.

Provides:
    - fetch_armenian_reading_text_task: Fetch Armenian text for a single Reading
      by scraping sacredtradition.am and matching by chapter/verse numbers.
"""

import logging

from celery import shared_task

from hub.models import Reading

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_armenian_reading_text_task')
def fetch_armenian_reading_text_task(self, reading_id: int):
    """Fetch Armenian Bible text for a single Reading from sacredtradition.am.

    Scrapes the Armenian readings page for the Reading's date, matches the
    reading by chapter/verse numbers, and updates ``text_hy`` on the Reading.

    Args:
        reading_id: Primary key of the Reading to fetch Armenian text for.
    """
    try:
        reading = Reading.objects.select_related("day", "day__church").get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    if not reading.day or not reading.day.date:
        logger.error("Reading %s has no associated day/date.", reading_id)
        return

    # Import here to avoid circular imports
    from hub.utils import scrape_armenian_reading_texts

    try:
        armenian_texts = scrape_armenian_reading_texts(
            reading.day.date,
            reading.day.church,
        )
    except Exception as e:
        logger.error(
            "Failed to scrape Armenian texts for Reading %s (date %s): %s",
            reading_id, reading.day.date, e,
        )
        raise self.retry(exc=e)

    if not armenian_texts:
        logger.warning(
            "No Armenian texts found for date %s (Reading %s).",
            reading.day.date, reading_id,
        )
        return

    # Match by chapter/verse numbers
    matched_text = None
    for entry in armenian_texts:
        if (entry["start_chapter"] == reading.start_chapter
                and entry["start_verse"] == reading.start_verse
                and entry["end_chapter"] == reading.end_chapter
                and entry["end_verse"] == reading.end_verse):
            matched_text = entry["text_hy"]
            break

    if not matched_text:
        logger.warning(
            "No matching Armenian text found for Reading %s (%s).",
            reading_id, reading.passage_reference,
        )
        return

    # Update text_hy via the i18n field
    reading.text_hy = matched_text
    reading.save(update_fields=["i18n"])

    logger.info(
        "Fetched Armenian text for Reading %s (%s).",
        reading_id, reading.passage_reference,
    )
