"""Celery tasks for fetching Bible verse text from API.Bible.

Provides:
    - fetch_reading_text_task: Fetch text for a single Reading.
    - refresh_all_reading_texts_task: Weekly scheduled task that refreshes stale
      readings, cleans up old readings, and logs an error summary.

Each Reading receives its own API call so that it gets a unique FUMS token,
as required by API.Bible's Fair Use Management System terms of use.
"""

import logging
import time
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from hub.models import Reading
from hub.services.bible_api_service import BibleAPIService

logger = logging.getLogger(__name__)


def _fetch_and_update_reading(service: BibleAPIService, reading: Reading) -> None:
    """Fetch a passage from API.Bible and update a single Reading.

    Each Reading gets its own API call so that it receives a unique FUMS token,
    as required by API.Bible's Fair Use Management System terms of use.

    Args:
        service: Initialized BibleAPIService instance.
        reading: The Reading instance to fetch text for.

    Raises:
        ValueError: If book name cannot be resolved to a USFM code.
        requests.HTTPError: On API errors.
    """
    usfm_id = BibleAPIService.resolve_book_name(reading.book)
    result = service.get_passage(
        usfm_id, reading.start_chapter, reading.start_verse,
        reading.end_chapter, reading.end_verse,
    )

    Reading.objects.filter(pk=reading.pk).update(
        text=result["content"],
        text_copyright=result["copyright"],
        text_version=result["version"],
        text_fetched_at=timezone.now(),
        fums_token=result.get("fums_token", ""),
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_reading_text_task')
def fetch_reading_text_task(self, reading_id: int):
    """Fetch Bible text for a single Reading.

    This task is triggered by the post_save signal when a new Reading is created.
    Each Reading gets its own API call and unique FUMS token.

    Args:
        reading_id: Primary key of the Reading to fetch text for.
    """
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    try:
        service = BibleAPIService()
    except ValueError as e:
        logger.error("Cannot initialize BibleAPIService: %s", e)
        return

    try:
        _fetch_and_update_reading(service, reading)
        logger.info(
            "Fetched text for Reading %s (%s).",
            reading_id, reading.passage_reference,
        )
    except ValueError as e:
        logger.error(
            "Book name mapping failed for Reading %s ('%s'): %s",
            reading_id, reading.book, e,
        )
        raise
    except Exception as e:
        logger.error(
            "API call failed for Reading %s (%s): %s",
            reading_id, reading.passage_reference, e,
        )
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=1, default_retry_delay=300, name='hub.tasks.refresh_all_reading_texts_task')
def refresh_all_reading_texts_task(self):
    """Refresh Bible text for all stale readings.

    Each Reading gets its own API call so that it receives a unique FUMS token,
    as required by API.Bible's Fair Use Management System terms of use.

    Scheduled weekly via Celery Beat. Steps:
        1. Cleanup: Delete oldest readings if count exceeds MAX_READINGS.
        2. Find stale readings (text_fetched_at is NULL or older than threshold).
        3. Fetch: One API call per reading, each with its own FUMS token.
        4. Log a structured error summary.
    """
    # --- Step 1: Cleanup old readings ---
    max_readings = getattr(settings, "MAX_READINGS", 2000)
    total_readings = Reading.objects.count()
    if total_readings > max_readings:
        excess = total_readings - max_readings
        oldest_ids = list(
            Reading.objects.order_by("day__date", "pk")
            .values_list("pk", flat=True)[:excess]
        )
        deleted_count, _ = Reading.objects.filter(pk__in=oldest_ids).delete()
        logger.info(
            "Cleanup: deleted %d oldest readings (was %d, now %d).",
            deleted_count, total_readings, Reading.objects.count(),
        )

    # --- Step 2: Find stale readings ---
    refresh_days = getattr(settings, "READING_TEXT_REFRESH_DAYS", 23)
    threshold = timezone.now() - timedelta(days=refresh_days)

    stale_readings = Reading.objects.filter(
        Q(text_fetched_at__isnull=True) | Q(text_fetched_at__lt=threshold)
    )
    stale_count = stale_readings.count()

    if stale_count == 0:
        logger.info("No stale readings found. Nothing to refresh.")
        return

    logger.info(
        "Found %d stale readings (threshold: %d days). Starting refresh...",
        stale_count, refresh_days,
    )

    # --- Step 3: Fetch each reading individually ---
    try:
        service = BibleAPIService()
    except ValueError as e:
        logger.error("Cannot initialize BibleAPIService: %s. Aborting refresh.", e)
        return

    api_calls = 0
    failures = []

    for reading in stale_readings.iterator():
        try:
            _fetch_and_update_reading(service, reading)
            api_calls += 1
        except Exception as e:
            failures.append({
                "reading_id": reading.pk,
                "passage": reading.passage_reference,
                "error": str(e),
            })
            logger.warning(
                "Failed to fetch Reading %s (%s): %s",
                reading.pk, reading.passage_reference, e,
            )

        # Small delay between API calls to avoid rate limiting
        time.sleep(0.5)

    # --- Step 4: Error summary ---
    logger.info(
        "Refresh complete: %d API calls, %d failures.",
        api_calls, len(failures),
    )

    if failures:
        failure_lines = []
        for f in failures:
            failure_lines.append(
                f"  - Reading {f['reading_id']} ({f['passage']}): {f['error']}"
            )
        failure_report = "\n".join(failure_lines)
        logger.error(
            "Reading text refresh failures (%d total):\n%s",
            len(failures), failure_report,
        )
