"""Celery tasks for fetching Bible verse text from API.Bible.

Provides:
    - fetch_reading_text_task: Fetch text for a single Reading.
      Useful for management commands or ad-hoc backfills.
    - refresh_all_reading_texts_task: Scheduled task that refreshes stale readings
      (>READING_TEXT_REFRESH_DAYS old) to comply with API.Bible terms of use,
      cleans up old readings, and logs an error summary.

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
from hub.services.reading_text_service import (
    fetch_all_reading_texts,
    fetch_english_text,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_reading_text_task')
def fetch_reading_text_task(self, reading_id: int):
    """Fetch Bible text for a single Reading.

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

    success = fetch_english_text(reading)
    if not success:
        logger.warning(
            "Could not fetch English text for Reading %s (%s).",
            reading_id, reading.passage_reference,
        )


@shared_task(bind=True, max_retries=1, default_retry_delay=300, name='hub.tasks.refresh_all_reading_texts_task')
def refresh_all_reading_texts_task(self):
    """Refresh Bible text (all languages) for all stale readings.

    Each Reading gets its own API call so that it receives a unique FUMS token,
    as required by API.Bible's Fair Use Management System terms of use.

    Scheduled weekly via Celery Beat. Steps:
        1. Cleanup: Delete oldest readings if count exceeds MAX_READINGS.
        2. Find stale readings (text_fetched_at is NULL or older than threshold).
        3. Fetch: Call every registered language fetcher per reading.
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

    stale_readings = Reading.objects.select_related("day", "day__church").filter(
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

    # --- Step 3: Fetch each reading (all languages) ---
    shared: dict = {}
    try:
        shared["service"] = BibleAPIService()
    except ValueError as exc:
        logger.error("Cannot initialize BibleAPIService: %s. English text will be skipped.", exc)

    api_calls = 0
    failures = []

    for reading in stale_readings.iterator():
        results = fetch_all_reading_texts(reading, **shared)
        if all(results.values()):
            api_calls += 1
        else:
            failed_langs = [lang for lang, ok in results.items() if not ok]
            failures.append({
                "reading_id": reading.pk,
                "passage": reading.passage_reference,
                "failed_langs": failed_langs,
            })

        # Small delay between API calls to avoid rate limiting
        time.sleep(0.5)

    # --- Step 4: Error summary ---
    logger.info(
        "Refresh complete: %d fully successful, %d with failures.",
        api_calls, len(failures),
    )

    if failures:
        failure_lines = []
        for f in failures:
            failure_lines.append(
                f"  - Reading {f['reading_id']} ({f['passage']}) — failed: {', '.join(f['failed_langs'])}"
            )
        failure_report = "\n".join(failure_lines)
        logger.error(
            "Reading text refresh failures (%d total):\n%s",
            len(failures), failure_report,
        )
