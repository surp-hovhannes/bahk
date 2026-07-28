"""Compute daily lectionary readings from the offline ``armenian_lectionary`` engine.

This replaces the previous sacredtradition.am scraping (``hub.utils.scrape_readings``) with an
in-process, offline call to :func:`armenian_lectionary.compute_armenian_lectionary`.  The engine
returns English citation strings (e.g. ``"Mark 15.42-16.1"``); we parse them into the same dict
shape the old scraper produced, so downstream persistence is unchanged.
"""
import logging
import re
from datetime import datetime

import armenian_lectionary
from django.conf import settings

import hub.models as models
from hub.services.reading_text_service import book_hy_for_book
from hub.utils import PARSER_REGEX, SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)

# The engine validates readings for 2001-2027 (the range guard lives in armenian_lectionary/app.py,
# not the engine, which will compute any date).  We only serve readings inside that validated
# window.  Overridable via settings so the range can widen without a code change.
LECTIONARY_MIN_YEAR = getattr(settings, "LECTIONARY_MIN_YEAR", 2001)
LECTIONARY_MAX_YEAR = getattr(settings, "LECTIONARY_MAX_YEAR", 2027)


def _parse_citation(reading_str: str) -> dict | None:
    """Parse an English citation string into reading components, or ``None`` if unparseable.

    Mirrors the parsing the old ``scrape_readings`` performed, including keeping only the first
    sub-reference of a comma-joined citation (e.g. ``"Daniel 3.1-23, Azariah 1-68"``).
    """
    if "," in reading_str:
        # Composite reading; keep the first sub-reference, matching historical scraper behavior.
        reading_str = reading_str.split(",")[0]

    reading_str = reading_str.strip()
    groups = re.search(PARSER_REGEX, reading_str)
    if groups is None:
        logger.error("Could not parse reading %r with regex %s", reading_str, PARSER_REGEX)
        return None

    try:
        book = groups.group(1).strip()
        # Remove decimal if start chapter provided; otherwise part of a book with 1 chapter.
        start_chapter = groups.group(2).strip(".") if groups.group(2) is not None else 1
        start_verse = groups.group(3)
        # Remove decimal if end chapter provided; otherwise it matches the start chapter.
        end_chapter = groups.group(4).strip(".") if groups.group(4) is not None else start_chapter
        end_verse = groups.group(5) if groups.group(5) is not None else start_verse
        return {
            "book": book,
            "book_en": book,
            "start_chapter": int(start_chapter),
            "start_verse": int(start_verse),
            "end_chapter": int(end_chapter),
            "end_verse": int(end_verse),
        }
    except Exception:
        logger.error(
            "Could not parse reading %r with regex %s. Skipping.",
            reading_str, PARSER_REGEX, exc_info=True,
        )
        return None


def get_daily_readings(date_obj, church) -> list[dict]:
    """Return the day's readings as dicts, computed offline from ``armenian_lectionary``.

    Drop-in replacement for ``hub.utils.scrape_readings``: returns a list of
    ``{"book", "book_en", "start_chapter", "start_verse", "end_chapter", "end_verse"}`` dicts.
    Returns ``[]`` for unsupported churches or dates outside the validated year window.
    """
    if church not in SUPPORTED_CHURCHES:
        logger.error(
            "Lectionary readings only set up for the following churches: %r. %s not supported.",
            SUPPORTED_CHURCHES, church,
        )
        return []

    # The engine does date arithmetic/comparisons; import_readings passes datetime objects.
    if isinstance(date_obj, datetime):
        date_obj = date_obj.date()

    if not (LECTIONARY_MIN_YEAR <= date_obj.year <= LECTIONARY_MAX_YEAR):
        logger.warning(
            "Date %s is outside the validated lectionary range %d-%d; no readings returned.",
            date_obj, LECTIONARY_MIN_YEAR, LECTIONARY_MAX_YEAR,
        )
        return []

    result = armenian_lectionary.compute_armenian_lectionary(date_obj)

    readings = []
    for citation in result.get("ReadingsList", []):
        parsed = _parse_citation(citation)
        if parsed is not None:
            readings.append(parsed)
    return readings


def persist_readings(day, readings: list[dict]) -> list[tuple["models.Reading", bool]]:
    """Get-or-create a ``Reading`` row for each entry in ``readings``, in order.

    ``readings`` must already be in the order the lectionary should display them (i.e. as
    returned by :func:`get_daily_readings`). Each ``Reading.sequence`` is (re)assigned from its
    position in this list, so display order tracks the engine's order even when the row already
    existed (e.g. it was imported previously, possibly in a different order).

    Returns a list of ``(reading_obj, created)`` tuples, in the same order as ``readings``.
    """
    results = []
    for index, reading in enumerate(readings):
        reading = dict(reading)
        # Extract and remove all book-related fields to handle them separately
        book_en = reading.pop("book_en", reading.get("book"))
        # get_daily_readings() never returns "book_hy" (it's not part of the engine's output
        # shape); resolve it ourselves from the same usfm_mapping.json that fetch_armenian_text()
        # uses, so it's populated at persistence time (see PR #461 review).
        book_hy = book_hy_for_book(book_en)
        # Remove 'book' from the dict to avoid using it in get_or_create lookup
        reading.pop("book", None)

        # Use explicit lookup with book_en to match the uniqueness constraint
        # (modeltrans treats 'book' as 'book_en' in the database)
        reading_obj, created = models.Reading.objects.get_or_create(
            day=day,
            book=book_en,  # This becomes book_en in the database
            start_chapter=reading["start_chapter"],
            start_verse=reading["start_verse"],
            end_chapter=reading["end_chapter"],
            end_verse=reading["end_verse"],
        )

        update_fields = []
        if reading_obj.sequence != index:
            reading_obj.sequence = index
            update_fields.append("sequence")
        if book_hy and not reading_obj.book_hy:
            reading_obj.book_hy = book_hy
            update_fields.append("i18n")
        if update_fields:
            reading_obj.save(update_fields=update_fields)

        results.append((reading_obj, created))
    return results
