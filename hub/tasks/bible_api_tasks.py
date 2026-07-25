"""Celery tasks for fetching Bible verse text from API.Bible.

Provides:
    - fetch_reading_text_task: Fetch text for a single Reading.
      Useful for management commands or ad-hoc backfills.
    - refresh_all_reading_texts_task: Scheduled task that refreshes at most
      READING_REFRESH_LIMIT stale readings (>READING_TEXT_REFRESH_DAYS old), nearest to
      today first, to comply with API.Bible terms of use, and logs an error summary.

Each Reading receives its own API call so that it gets a unique FUMS token,
as required by API.Bible's Fair Use Management System terms of use.
"""

import logging
import time

from celery import shared_task
from django.conf import settings

from hub.models import Reading
from hub.services.bible_api_service import BibleAPIService
from hub.services.reading_text_service import (
    bible_api_budgets,
    fetch_all_reading_texts,
    fetch_english_text,
    iter_readings_in_pk_order,
    reading_is_mappable,
    select_nearest_stale_reading_ids,
    stale_reading_queryset,
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
    """Refresh Bible text (all languages) for the stale readings nearest to today.

    Each Reading gets its own API call so that it receives a unique FUMS token,
    as required by API.Bible's Fair Use Management System terms of use.

    Scheduled weekly via Celery Beat. Steps:
        1. Find stale readings (text_fetched_at is NULL or older than threshold).
        2. Select at most READING_REFRESH_LIMIT of them, nearest to today first:
           today's and upcoming readings ascending, then past readings descending.
        3. Fetch: Call every registered language fetcher per reading, stopping early
           if English fetches start failing consecutively.
        4. Log a structured error summary.

    Readings are never deleted.  Pruning the table only caused rows to be re-created and
    re-fetched by the public readings view.  Rows this run does not reach stay expired;
    their text is blanked at serve time and re-fetched on demand within the daily budget.

    Spend is bounded twice over: by READING_REFRESH_LIMIT per run, and by the monthly
    ceiling in ``bible_api_budgets``.  The monthly budget matters because a failed fetch
    never records text_fetched_at — so without a ceiling, a run of quota rejections would
    leave every reading permanently stale and re-burn the whole quota every week.
    """
    # --- Step 1: Find stale readings ---
    refresh_days = getattr(settings, "READING_TEXT_REFRESH_DAYS", 23)
    limit = getattr(settings, "READING_REFRESH_LIMIT", 2000)
    max_consecutive_failures = getattr(settings, "READING_REFRESH_MAX_CONSECUTIVE_FAILURES", 10)

    stale_readings = stale_reading_queryset(refresh_days)
    stale_count = stale_readings.count()

    if stale_count == 0:
        logger.info("No stale readings found. Nothing to refresh.")
        return

    # --- Step 2: Select the nearest-to-today slice we can afford ---
    pks = select_nearest_stale_reading_ids(limit, queryset=stale_readings)

    logger.info(
        "Found %d stale readings (threshold: %d days); refreshing the %d nearest to today...",
        stale_count, refresh_days, len(pks),
    )

    # --- Step 3: Fetch each reading (all languages) ---
    shared: dict = {"budgets": bible_api_budgets(include_daily=False)}
    try:
        shared["service"] = BibleAPIService()
    except ValueError as exc:
        logger.error("Cannot initialize BibleAPIService: %s. English text will be skipped.", exc)

    api_calls = 0
    consecutive_failures = 0
    unmappable = 0
    aborted = False
    failures = []

    for reading in iter_readings_in_pk_order(pks):
        # Checked before the fetch so an unresolvable book name is not mistaken for the
        # API rejecting us: it fails before any HTTP request, costs nothing, and can
        # never succeed until BOOK_NAME_TO_USFM is extended.  Without this, a run that
        # selects only such readings — the steady state once everything else is fresh —
        # would trip the circuit breaker every week and cry wolf.
        mappable = reading_is_mappable(reading)
        if not mappable:
            unmappable += 1

        results = fetch_all_reading_texts(reading, **shared)

        if results.get("en"):
            # Count English calls, not all-languages-succeeded: this is the only
            # telemetry we have for API.Bible spend.
            api_calls += 1
            consecutive_failures = 0
        elif mappable:
            consecutive_failures += 1

        if not all(results.values()):
            failed_langs = [lang for lang, ok in results.items() if not ok]
            failures.append({
                "reading_id": reading.pk,
                "passage": reading.passage_reference,
                "failed_langs": failed_langs,
            })

        if consecutive_failures >= max_consecutive_failures:
            aborted = True
            logger.error(
                "Aborting refresh after %d consecutive English fetch failures "
                "(%d/%d readings attempted). API.Bible is likely rejecting calls "
                "(quota or credentials); continuing would burn the remaining quota "
                "without recording any text.",
                consecutive_failures, api_calls + len(failures), len(pks),
            )
            break

        # Small delay between API calls to avoid rate limiting
        time.sleep(0.5)

    # --- Step 4: Error summary ---
    logger.info(
        "Refresh %s: %d API.Bible calls, %d readings with failures "
        "(%d of %d stale readings selected).",
        "aborted" if aborted else "complete",
        api_calls, len(failures), len(pks), stale_count,
    )

    if unmappable:
        # These can never succeed and stay stale forever, so they are re-selected every
        # run. Surfaced separately so the fix (extend BOOK_NAME_TO_USFM) is discoverable
        # rather than buried among transient API errors.
        logger.warning(
            "%d of %d selected readings have book names with no USFM mapping. They cost "
            "no API calls but occupy refresh slots every run; add them to "
            "BOOK_NAME_TO_USFM in hub/constants.py.",
            unmappable, len(pks),
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
