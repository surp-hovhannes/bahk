"""Celery tasks for fetching Bible verse text from API.Bible.

Provides:
    - fetch_reading_text_task: Fetch text for a single Reading (and all duplicates).
    - refresh_all_reading_texts_task: Weekly scheduled task that refreshes stale
      readings, deduplicates API calls, cleans up old readings, and logs an error
      summary.
"""

import logging
import time
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from hub.constants import BOOK_NAME_TO_USFM
from hub.models import Reading
from hub.services.bible_api_service import BibleAPIService

logger = logging.getLogger(__name__)


def _fetch_and_update_passage(
    service: BibleAPIService,
    book_name: str,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> int:
    """Fetch a passage from API.Bible and update ALL matching Readings.

    Args:
        service: Initialized BibleAPIService instance.
        book_name: Book name as stored in Reading.book (English).
        start_chapter: Starting chapter number.
        start_verse: Starting verse number.
        end_chapter: Ending chapter number.
        end_verse: Ending verse number.

    Returns:
        Number of Reading rows updated.

    Raises:
        ValueError: If book name cannot be resolved to a USFM code.
        requests.HTTPError: On API errors.
    """
    usfm_id = BibleAPIService.resolve_book_name(book_name)
    result = service.get_passage(
        usfm_id, start_chapter, start_verse, end_chapter, end_verse
    )

    now = timezone.now()

    # Update ALL readings with this exact passage (across all days/years)
    updated = Reading.objects.filter(
        book=book_name,
        start_chapter=start_chapter,
        start_verse=start_verse,
        end_chapter=end_chapter,
        end_verse=end_verse,
    ).update(
        text=result["content"],
        text_copyright=result["copyright"],
        text_version=result["version"],
        text_fetched_at=now,
    )

    return updated


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def fetch_reading_text_task(self, reading_id: int):
    """Fetch Bible text for a single Reading and all duplicates of the same passage.

    This task is triggered by the post_save signal when a new Reading is created.
    It makes one API call and updates all Readings sharing the same passage
    (book, start_chapter, start_verse, end_chapter, end_verse).

    Args:
        reading_id: Primary key of the Reading to fetch text for.
    """
    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    # If another Reading with the same passage was recently fetched, reuse its data
    existing = Reading.objects.filter(
        book=reading.book,
        start_chapter=reading.start_chapter,
        start_verse=reading.start_verse,
        end_chapter=reading.end_chapter,
        end_verse=reading.end_verse,
        text_fetched_at__isnull=False,
    ).exclude(text="").first()

    if existing and existing.pk != reading.pk:
        # Copy text from the existing reading instead of making an API call
        Reading.objects.filter(pk=reading.pk).update(
            text=existing.text,
            text_copyright=existing.text_copyright,
            text_version=existing.text_version,
            text_fetched_at=existing.text_fetched_at,
        )
        logger.info(
            "Copied text for Reading %s from existing Reading %s (%s)",
            reading_id, existing.pk, reading.passage_reference,
        )
        return

    try:
        service = BibleAPIService()
    except ValueError as e:
        logger.error("Cannot initialize BibleAPIService: %s", e)
        return

    try:
        updated = _fetch_and_update_passage(
            service,
            reading.book,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )
        logger.info(
            "Fetched text for Reading %s (%s), updated %d reading(s).",
            reading_id, reading.passage_reference, updated,
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


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
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
            updated = _fetch_and_update_passage(
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
