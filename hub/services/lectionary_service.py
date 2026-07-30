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
