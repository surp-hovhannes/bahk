"""Retrieval and serving of Scripture text, keyed by passage rather than by reading.

Text lives in ``PassageText``, one row per ``(passage_key, language)``.  A ``Reading`` row
says *which* passage is read on a date; it does not carry the text.  That split is what
bounds retrieval cost: the lectionary emits ~1,500 readings a year forever but resolves to
only ~1,124 distinct passages across all years, so a passage-keyed store turns spend from
"grows with the table" into a constant.

Adding a language is two steps:

    1. Write a ``fetch_<lang>`` function with the standard signature below.
    2. Register it in ``TEXT_FETCHERS``.

Plus two optional ones: register a resource preparer in ``RESOURCE_PREPARERS`` if the
fetcher benefits from batch-level shared state (an HTTP session, a spend budget), and add
the language to ``settings.LANGUAGE_TEXT_MAX_AGE_DAYS`` if its source licence caps how old
served text may be.  Nothing else — no new columns, no modeltrans registration.

Each fetcher is *pure retrieval*: it takes a citation and returns a payload or ``None``,
and never writes.  ``store_passage_text`` is the single writer.  Keeping those apart is
what lets one retrieval serve every reading that cites the passage.

Fetchers receive the citation as written in the lectionary and apply their own edition's
versification.  This is deliberate: KJVAIC splits the Greek additions to Esther into a
separate ESG book numbered 1-7, while the Armenian Nor Ejmiatsin corpus keeps them inline
as EST chapters 10-16, so ``Esther 10:4-13`` is a different address in each.  Neither
mapping belongs in the shared key (see ``hub.constants.passage_key``).
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from hub.constants import BOOK_NAME_TO_USFM_NORMALIZED, normalize_book_name, passage_key
from hub.services.api_budget import DAY, MONTH, APIBudget
from hub.services.bible_api_service import BibleAPIService

logger = logging.getLogger(__name__)

ARMENIAN_TEXT_VERSION = "Նոր Էջմիածին"


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


def reading_citation(reading) -> tuple:
    """The ``(book, start_ch, start_v, end_ch, end_v)`` tuple fetchers take."""
    return (
        reading.book,
        reading.start_chapter,
        reading.start_verse,
        reading.end_chapter,
        reading.end_verse,
    )


# ------------------------------------------------------------------ #
#  Language fetchers -- pure retrieval, no writes
# ------------------------------------------------------------------ #
#
# Signature:  fetch_<lang>(book, start_ch, start_v, end_ch, end_v, **shared) -> dict | None
# Returns {"text", "version", "copyright", "fums_token"}, or None when retrieval failed.

def fetch_english(
    book,
    start_chapter,
    start_verse,
    end_chapter,
    end_verse,
    *,
    service: BibleAPIService | None = None,
    budgets: list[APIBudget] | None = None,
    stats: dict[str, int] | None = None,
    **_kwargs,
) -> dict[str, str] | None:
    """Retrieve English text for a passage from API.Bible.

    Applies API.Bible's own versification (the Esther -> ESG remap) and picks the edition
    (NKJV for canonical books, KJVAIC for the Apocrypha); both are properties of this
    source, not of the passage, so they live here rather than in the shared key.

    Args:
        book, start_chapter, ...: the citation as written in the lectionary.
        service: pre-initialized client, to share one HTTP session across a batch.
        budgets: spend budgets charged immediately before the call, and only once the
            passage has resolved -- so an unmappable book never burns a token.  If any
            budget refuses, no call is made and this returns None.
        stats: optional shared counter dict.  ``stats["attempted"]`` is incremented once
            per call that actually reaches API.Bible -- i.e. after an unmappable book or
            an exhausted budget has already returned, so callers can tell "we tried and
            it failed" apart from "we never tried" from the return value alone.

    Returns:
        The payload dict, or None if the book could not be resolved, a budget refused,
        or the API call failed.
    """
    if service is None:
        try:
            service = BibleAPIService()
        except ValueError as exc:
            logger.error("Cannot initialize BibleAPIService: %s", exc)
            return None

    # Resolve outside the API try-block: a mapping failure is a data problem, not an API
    # failure, and must neither consume budget nor be reported as an API error.
    try:
        passage = BibleAPIService.resolve_reading_passage(
            book, start_chapter, start_verse, end_chapter, end_verse,
        )
    except ValueError as exc:
        logger.error("Book name mapping failed for %r: %s", book, exc)
        return None

    for budget in budgets or ():
        if not budget.consume():
            logger.warning(
                "API.Bible %s budget (%d) exhausted; skipping fetch for %s %s:%s-%s:%s.",
                budget.period, budget.limit,
                book, start_chapter, start_verse, end_chapter, end_verse,
            )
            return None

    if stats is not None:
        stats["attempted"] = stats.get("attempted", 0) + 1

    try:
        result = service.get_passage(*passage)
    except Exception as exc:
        logger.error(
            "API call failed for %s %s:%s-%s:%s: %s",
            book, start_chapter, start_verse, end_chapter, end_verse, exc,
        )
        return None

    return {
        "text": result["content"],
        "version": result["version"],
        "copyright": result["copyright"],
        "fums_token": result.get("fums_token", ""),
    }


def fetch_armenian(
    book,
    start_chapter,
    start_verse,
    end_chapter,
    end_verse,
    **_kwargs,
) -> dict[str, str] | None:
    """Compose Armenian text for a passage from the offline ``BibleVerse`` corpus.

    Fully local -- indexed range scans over the Nor Ejmiatsin corpus, no network, no
    quota.  Uses the citation's own numbering: unlike KJVAIC, this corpus keeps the Greek
    additions to Esther inline in EST, so no remap applies.

    Returns None when the book has no USFM mapping or the corpus has no verses in range.
    """
    from hub.models import BibleVerse

    usfm = BOOK_NAME_TO_USFM_NORMALIZED.get(normalize_book_name(book))
    if not usfm:
        logger.warning("No USFM mapping for book %r; skipping Armenian text.", book)
        return None

    text = BibleVerse.compose_passage(
        BibleVerse.NOR_EJMIATSIN, usfm,
        start_chapter, start_verse, end_chapter, end_verse,
    )
    if not text:
        logger.warning(
            "No Armenian text in corpus for %s %s:%s-%s:%s.",
            book, start_chapter, start_verse, end_chapter, end_verse,
        )
        return None

    return {
        "text": text,
        "version": ARMENIAN_TEXT_VERSION,
        "copyright": "",
        "fums_token": "",  # locally composed; no usage-tracking token to report
    }


TEXT_FETCHERS: dict[str, callable] = {
    "en": fetch_english,
    "hy": fetch_armenian,
}


# ------------------------------------------------------------------ #
#  Shared-resource preparation
# ------------------------------------------------------------------ #

def bible_api_budgets(*, include_daily: bool = True) -> list[APIBudget]:
    """Spend budgets to charge for one API.Bible call, cheapest-to-refuse first.

    The monthly ceiling applies to every caller, so no failure mode can exhaust the plan
    quota.  The daily budget applies only to the public on-demand path, so traffic there
    cannot spend a whole month's allowance in a day.
    """
    budgets = []
    if include_daily:
        budgets.append(APIBudget(
            "bible_api", getattr(settings, "READING_FETCH_DAILY_BUDGET", 25), period=DAY,
        ))
    budgets.append(APIBudget(
        "bible_api", getattr(settings, "BIBLE_API_MONTHLY_BUDGET", 4500), period=MONTH,
    ))
    return budgets


def _prepare_english_resources(**_kwargs) -> dict[str, Any]:
    """Create the shared BibleAPIService and spend budgets for the English fetcher.

    Only ``prepare_shared_resources`` reaches this, and only the readings view calls that,
    so the daily budget is scoped to the public on-demand path.  The budgets are built
    outside the try-block: if the API key is missing we still want ``fetch_english`` to
    receive them rather than fall through to constructing its own unbudgeted service.
    """
    resources: dict[str, Any] = {"budgets": bible_api_budgets()}
    try:
        resources["service"] = BibleAPIService()
    except ValueError:
        logger.warning("BIBLE_API_KEY not configured; English text will be skipped.")
    return resources


# Armenian composes from the local BibleVerse corpus per passage (indexed range scans),
# so no batch-level shared resource is needed for "hy".
RESOURCE_PREPARERS: dict[str, callable] = {
    "en": _prepare_english_resources,
}


def prepare_shared_resources(date_obj=None, church=None) -> dict[str, Any]:
    """Build the shared-resource dict forwarded to every fetcher.

    ``date_obj``/``church`` are accepted and passed through for preparers that need them;
    no current preparer does, since retrieval is now keyed by passage rather than by date.
    """
    shared: dict[str, Any] = {}
    for lang, preparer in RESOURCE_PREPARERS.items():
        try:
            shared.update(preparer(date_obj=date_obj, church=church))
        except Exception:
            logger.exception("Failed to prepare resources for %s", lang)
    return shared


# ------------------------------------------------------------------ #
#  The single writer
# ------------------------------------------------------------------ #

def store_passage_text(key: str, language: str, payload: dict[str, str]):
    """Persist a fetcher payload as the text for *key* in *language*.

    One writer for every language, so a new source cannot introduce its own storage
    convention the way the English (queryset ``.update()``) and Armenian (``save()`` into
    the modeltrans JSON column) paths used to diverge.
    """
    from hub.models import PassageText

    obj, _created = PassageText.objects.update_or_create(
        passage_key=key,
        language=language,
        defaults={
            "text": payload.get("text", ""),
            "version": payload.get("version", ""),
            "copyright": payload.get("copyright", ""),
            "fums_token": payload.get("fums_token", ""),
            "fetched_at": timezone.now(),
        },
    )
    return obj


def fetch_passage_text(key: str, citation: tuple, langs=None, **shared) -> dict[str, bool]:
    """Retrieve and store text for one passage in each requested language.

    Args:
        key: the passage key the result is stored under.
        citation: ``(book, start_ch, start_v, end_ch, end_v)`` as written in the lectionary.
        langs: language codes to fetch; defaults to every registered language.
        **shared: forwarded to each fetcher (see ``prepare_shared_resources``).

    Returns:
        ``{lang: succeeded}``.  A language is False when its fetcher returned None --
        unresolvable book, refused budget, or a failed call.
    """
    if not key:
        # No USFM mapping, so no fetcher can address this passage.  Callers exclude these
        # up front; this guard keeps a stray one from being stored under the empty key,
        # where it would collide with every other unmappable passage.
        logger.warning("Refusing to fetch %r: no passage key (unmappable book).", citation)
        return {lang: False for lang in (langs or TEXT_FETCHERS)}

    results: dict[str, bool] = {}
    for lang in (langs if langs is not None else TEXT_FETCHERS):
        fetcher = TEXT_FETCHERS.get(lang)
        if fetcher is None:
            logger.error("No fetcher registered for language %r.", lang)
            results[lang] = False
            continue
        try:
            payload = fetcher(*citation, **shared)
        except Exception:
            logger.exception("Unhandled error fetching %s text for %s", lang, key)
            results[lang] = False
            continue
        if payload is None:
            results[lang] = False
            continue
        store_passage_text(key, lang, payload)
        logger.info("Stored %s text for %s.", lang, key)
        results[lang] = True
    return results


def fetch_all_reading_texts(reading, langs=None, **shared) -> dict[str, bool]:
    """Fetch text for the passage *reading* cites.  Convenience wrapper for Reading callers.

    The text is stored against the passage, so this also satisfies every other reading
    citing it -- on any date, for any church.
    """
    return fetch_passage_text(reading.passage_key, reading_citation(reading), langs, **shared)


# ------------------------------------------------------------------ #
#  Lookup and freshness
# ------------------------------------------------------------------ #

def load_passage_texts(keys) -> dict[tuple[str, str], Any]:
    """``{(passage_key, language): PassageText}`` for *keys*, in one query.

    The readings view resolves a whole day's response through this, so it must stay a
    single round trip regardless of how many readings the day has.
    """
    from hub.models import PassageText

    keys = {k for k in keys if k}
    if not keys:
        return {}
    return {
        (pt.passage_key, pt.language): pt
        for pt in PassageText.objects.filter(passage_key__in=keys)
    }


def languages_needing_fetch(key: str, passage_texts: dict, *, langs=None, now=None) -> list[str]:
    """Registered languages with no servable text for *key*: missing, empty, or expired.

    Per-language by construction.  English arriving from the shared store must not be read
    as "this passage is done" and suppress Armenian, which is the failure the old
    per-reading, English-only gate allowed.
    """
    if not key:
        return []
    needed = []
    for lang in (langs if langs is not None else TEXT_FETCHERS):
        existing = passage_texts.get((key, lang))
        if existing is None or not existing.text or existing.is_expired(now=now):
            needed.append(lang)
    return needed


def stale_passage_text_queryset(language: str, refresh_days: int | None = None):
    """``PassageText`` rows for *language* due for re-retrieval.

    Uses ``READING_TEXT_REFRESH_DAYS`` (23) rather than the 30-day serve-time cap, so a
    refreshed passage has a week of margin before it would be withheld from responses.
    """
    from datetime import timedelta

    from hub.models import PassageText

    if refresh_days is None:
        refresh_days = getattr(settings, "READING_TEXT_REFRESH_DAYS", 23)
    threshold = timezone.now() - timedelta(days=refresh_days)
    return PassageText.objects.filter(language=language).filter(
        Q(fetched_at__isnull=True) | Q(fetched_at__lt=threshold) | Q(text="")
    )


def reading_needs_text_fetch(reading, passage_texts: dict, *, now=None) -> bool:
    """True when any registered language lacks servable text for this reading's passage."""
    return bool(languages_needing_fetch(reading.passage_key, passage_texts, now=now))


def ensure_book_hy(reading) -> bool:
    """Top up ``Reading.book_hy`` from the corpus mapping.  Returns True if it changed.

    Stays on ``Reading`` rather than moving to ``PassageText``: it is the display name of
    the book on this reading, not retrieved passage text.  Rows created from the
    lectionary engine already carry it; this repairs older rows.
    """
    usfm = BOOK_NAME_TO_USFM_NORMALIZED.get(normalize_book_name(reading.book))
    if not usfm:
        return False
    hy_book = _usfm_to_hy_book_name().get(usfm)
    if not hy_book or reading.book_hy == hy_book:
        return False
    reading.book_hy = hy_book
    reading.save(update_fields=["i18n"])
    return True


# ------------------------------------------------------------------ #
#  Response field resolution
# ------------------------------------------------------------------ #

def get_reading_text_fields(reading, lang: str, *, passage_texts: dict) -> dict[str, str]:
    """Return the text/version/copyright/FUMS fields for *lang* as a dict.

    Falls back to English when the requested language is not registered.  Expired text is
    blanked rather than served: API.Bible's terms forbid displaying content cached beyond
    ``LANGUAGE_TEXT_MAX_AGE_DAYS['en']``, and text can outlive that window whenever a
    refresh run does not reach a passage or the spend budget is exhausted.

    Args:
        reading: a Reading instance.
        lang: ISO 639-1 code, e.g. ``"en"``, ``"hy"``.
        passage_texts: as returned by ``load_passage_texts``.

    Returns:
        ``text``, ``textVersion``, ``textCopyright``, ``fumsToken`` -- ready to merge into
        the API response.
    """
    blank = {"text": "", "textVersion": "", "textCopyright": "", "fumsToken": ""}

    # Resolve the language before checking expiry, so an unknown code falls back to English
    # *and* is held to English's freshness rule rather than skipping it.
    resolved = lang if lang in TEXT_FETCHERS else "en"

    existing = passage_texts.get((reading.passage_key, resolved))
    if existing is None:
        return blank
    if existing.is_expired():
        logger.info(
            "Suppressing expired %s text for %s (fetched %s).",
            resolved, reading.passage_key, existing.fetched_at,
        )
        return blank

    return {
        "text": existing.text or "",
        "textVersion": existing.version or "",
        "textCopyright": existing.copyright or "",
        "fumsToken": existing.fums_token or "",
    }
