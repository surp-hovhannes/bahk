"""Celery tasks for fetching Bible verse text from API.Bible.

Provides:
    - fetch_reading_text_task: Fetch text for a single Reading (and all duplicates).
      Useful for management commands or ad-hoc backfills.
    - refresh_all_reading_texts_task: Scheduled task that refreshes stale readings
      (>READING_TEXT_REFRESH_DAYS old) to comply with API.Bible terms of use,
      deduplicates API calls, cleans up old readings, and logs an error summary.
"""

import logging
import time
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from hub.models import Reading
from hub.services.bible_api_service import (
    BibleAPIService,
    fetch_and_update_passage,
    fetch_text_for_reading,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_reading_text_task')
def fetch_reading_text_task(self, reading_id: int):
    """Fetch Bible text for a single Reading and all duplicates of the same passage.

    NOTE: New readings created by the readings view now fetch text synchronously
    in the request cycle, so this task is no longer triggered by a post_save
    signal. It remains available for management commands and ad-hoc backfills.

    Args:
        reading_id: Primary key of the Reading to fetch text for.
    """
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    success = fetch_text_for_reading(reading)
    if not success:
        logger.warning(
            "Could not fetch text for Reading %s (%s).",
            reading_id, reading.passage_reference,
        )


@shared_task(bind=True, max_retries=1, default_retry_delay=300, name='hub.tasks.refresh_all_reading_texts_task')
def refresh_all_reading_texts_task(self):
    """Refresh Bible text for all stale readings, with deduplication.

    Scheduled weekly via Celery Beat. Steps:
        1. Cleanup: Delete oldest readings if count exceeds MAX_READINGS.
        2. Find stale readings (text_fetched_at is NULL or older than threshold).
        3. Deduplicate: Group by unique passage to minimize API calls.
        4. Fetch: One API call per unique passage, update ALL matching readings.
        5. Log a structured error summary.
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

    # --- Step 3: Deduplicate by unique passage ---
    # Get unique passage combinations from stale readings.
    # We use the English book name (stored in `book` column) for grouping.
    unique_passages = (
        stale_readings
        .values("book", "start_chapter", "start_verse", "end_chapter", "end_verse")
        .distinct()
    )
    unique_passage_list = list(unique_passages)

    logger.info(
        "%d stale readings map to %d unique passages.",
        stale_count, len(unique_passage_list),
    )

    # --- Step 4: Fetch and fan out ---
    try:
        service = BibleAPIService()
    except ValueError as e:
        logger.error("Cannot initialize BibleAPIService: %s. Aborting refresh.", e)
        return

    api_calls = 0
    total_updated = 0
    failures = []

    for passage in unique_passage_list:
        book_name = passage["book"]
        start_ch = passage["start_chapter"]
        start_v = passage["start_verse"]
        end_ch = passage["end_chapter"]
        end_v = passage["end_verse"]

        passage_ref = f"{book_name} {start_ch}:{start_v}-{end_ch}:{end_v}"

        try:
            updated = fetch_and_update_passage(
                service, book_name, start_ch, start_v, end_ch, end_v,
            )
            api_calls += 1
            total_updated += updated
        except Exception as e:
            failures.append({
                "book": book_name,
                "passage": passage_ref,
                "error": str(e),
            })
            logger.warning("Failed to fetch %s: %s", passage_ref, e)

        # Small delay between API calls to avoid rate limiting
        time.sleep(0.5)

    # --- Step 5: Error summary ---
    logger.info(
        "Refresh complete: %d API calls, %d readings updated, %d failures.",
        api_calls, total_updated, len(failures),
    )

    if failures:
        failure_lines = []
        for f in failures:
            failure_lines.append(f"  - {f['passage']} ({f['book']}): {f['error']}")
        failure_report = "\n".join(failure_lines)
        logger.error(
            "Reading text refresh failures (%d total):\n%s",
            len(failures), failure_report,
        )
