"""Unified service for fetching Bible reading text in all supported languages.

Provides a registry of per-language fetchers and an orchestrator that calls
them all for a given Reading.  Adding a new language is a three-step process:

    1. Write a ``fetch_<lang>_text`` function with the standard signature.
    2. Register it in ``TEXT_FETCHERS``.
    3. (Optional) Register a resource preparer in ``RESOURCE_PREPARERS`` if the
       fetcher benefits from batch-level shared state (e.g. a scraped page or
       HTTP session that can be reused across multiple readings).
    4. (Optional) Register the language in ``EXPIRING_LANGUAGES`` if its source
       licence caps how old served text may be.

The view calls ``prepare_shared_resources`` once per batch, then
``fetch_all_reading_texts`` once per reading.  ``get_reading_text_fields``
resolves model fields for the API response without hard-coding language names.

``prepare_shared_resources`` also carries the API.Bible spend budgets, and only the
readings view calls it \u2014 so the budgets gate the public on-demand path and nothing else.
The weekly refresh task builds its own shared dict and charges only the monthly budget.
"""

import json
import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from hub.constants import BOOK_NAME_TO_USFM_NORMALIZED, normalize_book_name
from hub.services.api_budget import DAY, MONTH, APIBudget
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

def reading_is_mappable(reading) -> bool:
    """True when the reading's book resolves to a USFM id, i.e. a fetch would reach the API.

    Lets callers tell "API.Bible refused us" apart from "we cannot name this book".  The
    latter fails before any HTTP request, so it is a data problem to fix in
    ``BOOK_NAME_TO_USFM`` — not a signal that the API is unhealthy.  Pure dict lookups,
    no I/O, so it is cheap to call alongside a fetch.
    """
    try:
        BibleAPIService.resolve_reading_passage(
            reading.book,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )
    except ValueError:
        return False
    return True


def fetch_english_text(
    reading,
    *,
    service: BibleAPIService | None = None,
    budgets: list[APIBudget] | None = None,
    stats: dict[str, int] | None = None,
    **_kwargs,
) -> bool:
    """Fetch English Bible text from API.Bible for a single Reading.

    Each Reading gets its own API call so that it receives a unique FUMS
    token, as required by API.Bible's Fair Use Management System terms.

    Args:
        reading: A saved Reading model instance.
        service: Optional pre-initialized BibleAPIService (shares the HTTP
                 session across a batch of readings).
        budgets: Optional spend budgets to charge before calling the API.  They are
                 consumed only once the passage has resolved and immediately before
                 the call, so an unmappable book name never burns a token.  If any
                 budget refuses, no call is made and this returns False.
        stats: Optional shared counter dict.  ``stats["attempted"]`` is incremented
               once per call that actually reaches API.Bible — i.e. after an unmappable
               book or an exhausted budget has already returned, so callers can tell
               "we tried and it failed" apart from "we never tried" using only the
               boolean return value.

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

    # Resolve first, outside the API try-block: a mapping failure is a data problem, not
    # an API failure, and must not consume budget or be reported as an API error.
    try:
        passage = BibleAPIService.resolve_reading_passage(
            reading.book,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )
    except ValueError as exc:
        logger.error(
            "Book name mapping failed for Reading %s ('%s'): %s",
            reading.pk, reading.book, exc,
        )
        return False

    for budget in budgets or ():
        if not budget.consume():
            logger.warning(
                "API.Bible %s budget (%d) exhausted; skipping fetch for Reading %s (%s).",
                budget.period, budget.limit, reading.pk, reading.passage_reference,
            )
            return False

    if stats is not None:
        stats["attempted"] = stats.get("attempted", 0) + 1

    try:
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

def bible_api_budgets(*, include_daily: bool = True) -> list[APIBudget]:
    """Spend budgets to charge for one API.Bible call, cheapest-to-refuse first.

    The monthly ceiling applies to every caller, so no failure mode — including a run of
    quota rejections, which never record ``text_fetched_at`` and therefore retry forever —
    can exhaust the plan quota.  The daily budget applies only to the public on-demand
    path, so traffic there cannot spend a whole month's allowance in a day.
    """
    budgets = []
    if include_daily:
        budgets.append(APIBudget(
            "bible_api", getattr(settings, "READING_FETCH_DAILY_BUDGET", 75), period=DAY,
        ))
    budgets.append(APIBudget(
        "bible_api", getattr(settings, "BIBLE_API_MONTHLY_BUDGET", 4500), period=MONTH,
    ))
    return budgets


def _prepare_english_resources(**_kwargs) -> dict[str, Any]:
    """Create the shared BibleAPIService and spend budgets for the English fetcher.

    Only ``prepare_shared_resources`` reaches this, and only the readings view calls that,
    so the daily budget is scoped to the public on-demand path.  The budgets are built
    outside the try-block: if the API key is missing we still want ``fetch_english_text``
    to receive them rather than fall through to constructing its own unbudgeted service.
    """
    resources: dict[str, Any] = {"budgets": bible_api_budgets()}
    try:
        resources["service"] = BibleAPIService()
    except ValueError:
        logger.warning("BIBLE_API_KEY not configured; English text will be skipped.")
    return resources


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
#  Stale-reading selection
# ------------------------------------------------------------------ #

def stale_reading_queryset(refresh_days: int | None = None):
    """Readings whose English text is missing or older than the refresh threshold."""
    from hub.models import Reading as ReadingModel

    if refresh_days is None:
        refresh_days = getattr(settings, "READING_TEXT_REFRESH_DAYS", 23)
    threshold = timezone.now() - timedelta(days=refresh_days)
    return ReadingModel.objects.filter(
        Q(text_fetched_at__isnull=True) | Q(text_fetched_at__lt=threshold)
    )


def select_nearest_stale_reading_ids(limit: int, *, today=None, queryset=None) -> list[int]:
    """PKs of at most *limit* stale readings, nearest to today first.

    Today's and upcoming readings come first (ascending by date), then past readings
    (descending), until *limit* is reached.  Nobody browses far into the past, so
    spending the refresh allowance on the dates people actually open is what keeps
    served text inside API.Bible's freshness window.

    Uses two ordered queries rather than an ``Abs(day__date - today)`` annotation so the
    same code runs on PostgreSQL (production) and SQLite (tests).
    """
    if limit <= 0:
        return []

    qs = stale_reading_queryset() if queryset is None else queryset
    today = today or timezone.localdate()

    upcoming = list(
        qs.filter(day__date__gte=today)
        .order_by("day__date", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    remaining = limit - len(upcoming)
    if remaining <= 0:
        return upcoming

    past = list(
        qs.filter(day__date__lt=today)
        .order_by("-day__date", "-pk")
        .values_list("pk", flat=True)[:remaining]
    )
    return upcoming + past


def iter_readings_in_pk_order(pks, chunk_size: int = 100):
    """Yield Readings for *pks* in exactly that order, loading *chunk_size* at a time.

    Chunked rather than one big ``list()``: a refresh run sleeps between calls and can
    take tens of minutes, so each batch is re-read just before use instead of holding
    thousands of rows (each carrying full verse text) in memory the whole time.
    """
    from hub.models import Reading as ReadingModel

    pks = list(pks)
    for start in range(0, len(pks), chunk_size):
        chunk = pks[start:start + chunk_size]
        by_pk = {
            reading.pk: reading
            for reading in ReadingModel.objects.select_related("day", "day__church").filter(pk__in=chunk)
        }
        for pk in chunk:
            reading = by_pk.get(pk)
            if reading is not None:  # skip rows deleted between selection and load
                yield reading


# ------------------------------------------------------------------ #
#  Response field resolution
# ------------------------------------------------------------------ #

# Maps language code → (text_field, version_field, copyright_field, fums_field)
# English uses the base field names; other languages use the <field>_<lang> convention.
LANGUAGE_FIELD_MAP: dict[str, tuple[str, str, str, str]] = {
    "en": ("text",    "text_version",    "text_copyright",    "fums_token"),
    "hy": ("text_hy", "text_hy_version", "text_hy_copyright", "text_hy_fums_token"),
}

# Languages whose source licence caps how old served text may be, mapped to the model
# field holding the last-fetch timestamp.  A language absent from this dict never
# expires.  Registering here is the optional fourth step when adding a language.
EXPIRING_LANGUAGES: dict[str, str] = {
    "en": "text_fetched_at",  # API.Bible: 30-day freshness requirement
}


def text_is_expired(reading, lang: str, *, now=None) -> bool:
    """True when *lang*'s text on *reading* is past its licence's freshness cap.

    A missing timestamp counts as expired: we cannot show that the text is fresh enough
    to serve, so we treat it the same as stale.
    """
    fetched_at_field = EXPIRING_LANGUAGES.get(lang)
    if fetched_at_field is None:
        return False
    fetched_at = getattr(reading, fetched_at_field, None)
    if fetched_at is None:
        return True
    max_age = getattr(settings, "READING_TEXT_MAX_AGE_DAYS", 30)
    return fetched_at < (now or timezone.now()) - timedelta(days=max_age)


def reading_needs_text_fetch(reading, *, now=None) -> bool:
    """True when any freshness-limited language on *reading* has expired.

    The readings view uses this to decide whether to spend an on-demand API call.
    """
    return any(text_is_expired(reading, lang, now=now) for lang in EXPIRING_LANGUAGES)


def get_reading_text_fields(reading, lang: str) -> dict[str, str]:
    """Return the text/version/copyright/FUMS fields for *lang* as a dict.

    Falls back to English if the requested language is not in the registry.  Expired text
    is blanked rather than served: API.Bible's terms forbid displaying content cached for
    more than ``READING_TEXT_MAX_AGE_DAYS``, and text can outlive that window whenever a
    refresh run does not reach a reading or the spend budget is exhausted.

    Args:
        reading: A Reading model instance.
        lang: ISO 639-1 language code (e.g. ``"en"``, ``"hy"``).

    Returns:
        Dict with keys ``text``, ``textVersion``, ``textCopyright``,
        ``fumsToken`` — ready to be merged into the API response.
    """
    # Resolve the language before checking expiry, so an unknown code falls back to
    # English *and* is held to English's freshness rule rather than skipping it.
    resolved = lang if lang in LANGUAGE_FIELD_MAP else "en"

    if text_is_expired(reading, resolved):
        logger.info(
            "Suppressing expired %s text for Reading %s (fetched %s).",
            resolved, reading.pk, getattr(reading, EXPIRING_LANGUAGES[resolved], None),
        )
        return {"text": "", "textVersion": "", "textCopyright": "", "fumsToken": ""}

    text_f, version_f, copyright_f, fums_f = LANGUAGE_FIELD_MAP[resolved]
    return {
        "text": getattr(reading, text_f, "") or "",
        "textVersion": getattr(reading, version_f, "") or "",
        "textCopyright": getattr(reading, copyright_f, "") or "",
        "fumsToken": getattr(reading, fums_f, "") or "",
    }
