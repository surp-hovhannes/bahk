"""Unified service for fetching Bible reading text in all supported languages.

Provides a registry of per-language fetchers and an orchestrator that calls
them all for a given Reading.  Adding a new language is a three-step process:

    1. Write a ``fetch_<lang>_text`` function with the standard signature.
    2. Register it in ``TEXT_FETCHERS``.
    3. (Optional) Register a resource preparer in ``RESOURCE_PREPARERS`` if the
       fetcher benefits from batch-level shared state (e.g. a scraped page or
       HTTP session that can be reused across multiple readings).

The view calls ``prepare_shared_resources`` once per batch, then
``fetch_all_reading_texts`` once per reading.  ``get_reading_text_fields``
resolves model fields for the API response without hard-coding language names.
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


# ------------------------------------------------------------------ #
#  Shared-resource preparation
# ------------------------------------------------------------------ #

def _prepare_english_resources(**_kwargs) -> dict[str, Any]:
    """Create a shared BibleAPIService instance for the English fetcher."""
    try:
        return {"service": BibleAPIService()}
    except ValueError:
        logger.warning("BIBLE_API_KEY not configured; English text will be skipped.")
        return {}


def _prepare_armenian_resources(*, date_obj, church, **_kwargs) -> dict[str, Any]:
    """Pre-scrape the Armenian readings page once for the whole batch."""
    from hub.utils import scrape_armenian_reading_texts

    try:
        return {"armenian_texts": scrape_armenian_reading_texts(date_obj, church)}
    except Exception:
        logger.warning(
            "Failed to scrape Armenian texts for %s; Armenian text will be skipped.",
            date_obj,
            exc_info=True,
        )
        return {}


RESOURCE_PREPARERS: dict[str, callable] = {
    "en": _prepare_english_resources,
    "hy": _prepare_armenian_resources,
}


def prepare_shared_resources(date_obj, church) -> dict[str, Any]:
    """Build the shared-resource dict consumed by ``fetch_all_reading_texts``.

    Iterates over ``RESOURCE_PREPARERS`` and merges their results into a
    single dict.  Each preparer receives ``date_obj`` and ``church`` as
    keyword arguments and returns a dict of key/value pairs to forward to
    the fetchers.

    Args:
        date_obj: The date for which readings are being fetched.
        church: The Church instance.

    Returns:
        Dict of shared resources, e.g.
        ``{"service": <BibleAPIService>, "armenian_texts": [...]}``.
    """
    shared: dict[str, Any] = {}
    for lang, preparer in RESOURCE_PREPARERS.items():
        try:
            shared.update(preparer(date_obj=date_obj, church=church))
        except Exception:
            logger.exception("Failed to prepare resources for %s", lang)
    return shared


# ------------------------------------------------------------------ #
#  Orchestrator
# ------------------------------------------------------------------ #

def fetch_all_reading_texts(reading, **shared_resources) -> dict[str, bool]:
    """Fetch reading text for every registered language.

    Iterates over ``TEXT_FETCHERS`` and calls each fetcher, forwarding any
    ``shared_resources`` as keyword arguments.  Each fetcher accepts
    ``**_kwargs`` so unknown keys are silently ignored.

    Args:
        reading: A saved Reading model instance.
        **shared_resources: Keyword arguments forwarded to every fetcher.
            Typically produced by ``prepare_shared_resources()``.

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


# ------------------------------------------------------------------ #
#  Response field resolution
# ------------------------------------------------------------------ #

# Maps language code → (text_field, version_field, copyright_field, fums_field)
# English uses the base field names; other languages use the <field>_<lang> convention.
LANGUAGE_FIELD_MAP: dict[str, tuple[str, str, str, str]] = {
    "en": ("text",    "text_version",    "text_copyright",    "fums_token"),
    "hy": ("text_hy", "text_hy_version", "text_hy_copyright", "text_hy_fums_token"),
}


def get_reading_text_fields(reading, lang: str) -> dict[str, str]:
    """Return the text/version/copyright/FUMS fields for *lang* as a dict.

    Falls back to English if the requested language is not in the registry.

    Args:
        reading: A Reading model instance.
        lang: ISO 639-1 language code (e.g. ``"en"``, ``"hy"``).

    Returns:
        Dict with keys ``text``, ``textVersion``, ``textCopyright``,
        ``fumsToken`` — ready to be merged into the API response.
    """
    text_f, version_f, copyright_f, fums_f = LANGUAGE_FIELD_MAP.get(
        lang, LANGUAGE_FIELD_MAP["en"],
    )
    return {
        "text": getattr(reading, text_f, "") or "",
        "textVersion": getattr(reading, version_f, "") or "",
        "textCopyright": getattr(reading, copyright_f, "") or "",
        "fumsToken": getattr(reading, fums_f, "") or "",
    }
