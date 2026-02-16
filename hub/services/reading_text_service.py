"""Unified service for fetching Bible reading text in all supported languages.

Provides a registry of per-language fetchers and an orchestrator that calls
them all for a given Reading.  Adding a new language is a two-step process:

    1. Write a ``fetch_<lang>_text`` function with the standard signature.
    2. Register it in ``TEXT_FETCHERS``.

The view calls ``fetch_all_reading_texts`` once after creating readings.
Celery tasks and management commands can call individual fetchers directly.
"""

import logging
from typing import Any

from django.utils import timezone

from hub.services.bible_api_service import BibleAPIService

logger = logging.getLogger(__name__)

ARMENIAN_TEXT_VERSION = "\u0546\u0578\u0580 \u0537\u057b\u0574\u056b\u0561\u056e\u056b\u0576"


# ------------------------------------------------------------------ #
#  Individual language fetchers
# ------------------------------------------------------------------ #

def fetch_english_text(reading, *, service: BibleAPIService | None = None, **_kwargs) -> bool:
    """Fetch English Bible text from API.Bible for a single Reading.

    Each Reading gets its own API call so that it receives a unique FUMS
    token, as required by API.Bible's Fair Use Management System terms.

    Args:
        reading: A saved Reading model instance.
        service: Optional pre-initialized BibleAPIService (shares the HTTP
                 session across a batch of readings).

    Returns:
        True if text was successfully fetched, False otherwise.
    """
    from hub.models import Reading as ReadingModel

    if service is None:
        try:
            service = BibleAPIService()
        except ValueError as exc:
            logger.error("Cannot initialize BibleAPIService: %s", exc)
            return False

    try:
        usfm_id = BibleAPIService.resolve_book_name(reading.book)
        result = service.get_passage(
            usfm_id,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )

        ReadingModel.objects.filter(pk=reading.pk).update(
            text=result["content"],
            text_copyright=result["copyright"],
            text_version=result["version"],
            text_fetched_at=timezone.now(),
            fums_token=result.get("fums_token", ""),
        )
        logger.info(
            "Fetched EN text for Reading %s (%s).",
            reading.pk, reading.passage_reference,
        )
        return True
    except ValueError as exc:
        logger.error(
            "Book name mapping failed for Reading %s ('%s'): %s",
            reading.pk, reading.book, exc,
        )
        return False
    except Exception as exc:
        logger.error(
            "API call failed for Reading %s (%s): %s",
            reading.pk, reading.passage_reference, exc,
        )
        return False


def fetch_armenian_text(
    reading,
    *,
    armenian_texts: list[dict[str, Any]] | None = None,
    **_kwargs,
) -> bool:
    """Fetch Armenian Bible text from sacredtradition.am for a single Reading.

    Args:
        reading: A saved Reading model instance (must have ``day`` with a date).
        armenian_texts: Optional pre-fetched list of scraped Armenian texts for
                        the reading's date.  When processing a batch of readings
                        for the same date, pass the same list to avoid scraping
                        the page repeatedly.

    Returns:
        True if text was successfully matched and saved, False otherwise.
    """
    from hub.utils import scrape_armenian_reading_texts

    if not reading.day or not reading.day.date:
        logger.error("Reading %s has no associated day/date.", reading.pk)
        return False

    if armenian_texts is None:
        try:
            armenian_texts = scrape_armenian_reading_texts(
                reading.day.date,
                reading.day.church,
            )
        except Exception as exc:
            logger.error(
                "Failed to scrape Armenian texts for Reading %s (date %s): %s",
                reading.pk, reading.day.date, exc,
            )
            return False

    if not armenian_texts:
        logger.warning(
            "No Armenian texts found for date %s (Reading %s).",
            reading.day.date, reading.pk,
        )
        return False

    matched_text = None
    for entry in armenian_texts:
        if (
            entry["start_chapter"] == reading.start_chapter
            and entry["start_verse"] == reading.start_verse
            and entry["end_chapter"] == reading.end_chapter
            and entry["end_verse"] == reading.end_verse
        ):
            matched_text = entry["text_hy"]
            break

    if not matched_text:
        logger.warning(
            "No matching Armenian text found for Reading %s (%s).",
            reading.pk, reading.passage_reference,
        )
        return False

    reading.text_hy = matched_text
    reading.text_hy_version = ARMENIAN_TEXT_VERSION
    reading.text_hy_fetched_at = timezone.now()
    reading.save(update_fields=["i18n", "text_hy_version", "text_hy_fetched_at"])

    logger.info(
        "Fetched HY text for Reading %s (%s).",
        reading.pk, reading.passage_reference,
    )
    return True


# ------------------------------------------------------------------ #
#  Registry & orchestrator
# ------------------------------------------------------------------ #

TEXT_FETCHERS: dict[str, callable] = {
    "en": fetch_english_text,
    "hy": fetch_armenian_text,
}


def fetch_all_reading_texts(reading, **shared_resources) -> dict[str, bool]:
    """Fetch reading text for every registered language.

    Iterates over ``TEXT_FETCHERS`` and calls each fetcher, forwarding any
    ``shared_resources`` as keyword arguments (e.g. a pre-initialized
    ``service`` for English, pre-scraped ``armenian_texts`` for Armenian).

    Args:
        reading: A saved Reading model instance.
        **shared_resources: Keyword arguments forwarded to every fetcher.
            Recognised keys (each fetcher ignores unknown kwargs via **_kwargs):
                service:        BibleAPIService instance (English fetcher)
                armenian_texts: list of scraped dicts   (Armenian fetcher)

    Returns:
        Dict mapping language code to success boolean, e.g.
        ``{"en": True, "hy": False}``.
    """
    results = {}
    for lang, fetcher in TEXT_FETCHERS.items():
        try:
            results[lang] = fetcher(reading, **shared_resources)
        except Exception:
            logger.exception(
                "Unhandled error fetching %s text for Reading %s",
                lang, reading.pk,
            )
            results[lang] = False
    return results
