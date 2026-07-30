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

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from hub.constants import BOOK_NAME_TO_USFM_NORMALIZED, normalize_book_name
from hub.services.bible_api_service import BibleAPIService

logger = logging.getLogger(__name__)

ARMENIAN_TEXT_VERSION = "\u0546\u0578\u0580 \u0537\u057b\u0574\u056b\u0561\u056e\u056b\u0576"


@lru_cache(maxsize=1)
def usfm_to_hy_book_name() -> dict[str, str]:
    """USFM book id -> Armenian (Nor Ejmiatsin) book display name, from the corpus mapping.

    Loaded once from the version-controlled ``usfm_mapping.json`` so we can keep populating
    ``Reading.book_hy`` after retiring the Armenian scrape.  Returns ``{}`` if unavailable.
    """
    path = Path(settings.BASE_DIR) / "hub" / "data" / "bible_hy" / "usfm_mapping.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read Armenian book-name mapping at %s", path, exc_info=True)
        return {}
    return {row["usfm"]: row["name_hy"] for row in rows if row.get("usfm") and row.get("name_hy")}


def book_hy_for_book(book_en: str) -> str | None:
    """Resolve the Armenian (Nor Ejmiatsin) display name for an English book name.

    Used at persistence time (``import_readings``, ``GetDailyReadingsForDate``) so
    ``Reading.book_hy`` is populated immediately from the same version-controlled mapping
    ``fetch_armenian_text`` uses, rather than depending on a ``book_hy`` key that
    ``get_daily_readings`` never produces (see PR #461 review).
    """
    usfm = BOOK_NAME_TO_USFM_NORMALIZED.get(normalize_book_name(book_en))
    if not usfm:
        return None
    return usfm_to_hy_book_name().get(usfm)


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
        passage = BibleAPIService.resolve_reading_passage(
            reading.book,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )
        result = service.get_passage(
            *passage,
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


def fetch_armenian_text(reading, **_kwargs) -> bool:
    """Compose Armenian Bible text for a single Reading from the offline ``BibleVerse`` corpus.

    Maps the reading's (English) book name to a USFM id and composes the passage text — with
    inline ``[verse]`` markers, matching the historical format — from the local ``BibleVerse``
    table.  Fully offline; no network access.  Also refreshes ``book_hy`` from the corpus mapping.

    Args:
        reading: A saved Reading model instance.

    Returns:
        True if text was composed and saved, False otherwise.
    """
    from hub.models import BibleVerse

    usfm = BOOK_NAME_TO_USFM_NORMALIZED.get(normalize_book_name(reading.book))
    if not usfm:
        logger.warning(
            "No USFM mapping for book %r (Reading %s); skipping Armenian text.",
            reading.book, reading.pk,
        )
        return False

    text = BibleVerse.compose_passage(
        BibleVerse.NOR_EJMIATSIN, usfm,
        reading.start_chapter, reading.start_verse,
        reading.end_chapter, reading.end_verse,
    )
    if not text:
        logger.warning(
            "No Armenian text in corpus for Reading %s (%s).",
            reading.pk, reading.passage_reference,
        )
        return False

    # text_hy and book_hy are modeltrans virtual fields; the concrete ``i18n`` JSON column stores
    # both, so a single save with ``i18n`` in update_fields persists them together.
    reading.text_hy = text
    reading.text_hy_version = ARMENIAN_TEXT_VERSION
    reading.text_hy_fetched_at = timezone.now()

    hy_book = usfm_to_hy_book_name().get(usfm)
    if hy_book and reading.book_hy != hy_book:
        reading.book_hy = hy_book

    reading.save(update_fields=["i18n", "text_hy_version", "text_hy_fetched_at"])

    logger.info(
        "Composed HY text for Reading %s (%s).",
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


# Armenian text is composed from the local BibleVerse corpus per-reading (indexed range scans),
# so no batch-level shared resource is needed for "hy".
RESOURCE_PREPARERS: dict[str, callable] = {
    "en": _prepare_english_resources,
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
        Dict of shared resources, e.g. ``{"service": <BibleAPIService>}``.
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
