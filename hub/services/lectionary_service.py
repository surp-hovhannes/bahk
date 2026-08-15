"""Compute daily lectionary readings from the offline ``armenian_lectionary`` engine.

This replaces the previous sacredtradition.am scraping with an in-process, offline call to
:func:`armenian_lectionary.compute_armenian_lectionary`.

The engine's ``"ReadingsRefs"`` (new in armenian-lectionary 1.3.0) already gives every reading as
``{book, start_chapter, start_verse, end_chapter, end_verse, citation}``, so this module no longer
parses the citation strings in ``"ReadingsList"`` back apart itself.  ``book`` is the canonical
English head regardless of ``language``, and a composite citation -- the corpus contains exactly
one, ``"Daniel 3.1-23, Azariah. 1-68"`` -- arrives pre-split into one ref per sub-reference, each
carrying the unsplit ``citation`` as a back-pointer to their shared ``ReadingsList`` entry.

Letting the engine do the splitting is what retires the local regex: it also removes the need to
strip a trailing period off a sub-reference book name (``"Azariah."``), and it means an
unrecognised book or an unparseable verse range is a hard ``ValueError`` at the engine's own
citation-book list rather than a silently dropped reading here.
"""
import logging
from datetime import datetime

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR
from django.conf import settings

import hub.models as models
from hub.services.reading_text_service import book_hy_for_book
from hub.utils import SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)

# Readings are validated only for the engine's supported range, and as of 1.3.0 the engine itself
# raises ValueError outside it instead of returning placeholder text.  Default to the engine's own
# bounds so the two cannot drift; keep the settings override so the window can be narrowed (or
# widened alongside a new engine release) without a code change.
LECTIONARY_MIN_YEAR = getattr(settings, "LECTIONARY_MIN_YEAR", MIN_YEAR)
LECTIONARY_MAX_YEAR = getattr(settings, "LECTIONARY_MAX_YEAR", MAX_YEAR)

# The span fields taken from each engine ref.  "citation" is deliberately not among them: Reading
# rows are keyed by their (book, chapter, verse) span, and the two halves of the one composite
# citation must persist as two distinct readings rather than share a display string.
_REF_SPAN_FIELDS = ("start_chapter", "start_verse", "end_chapter", "end_verse")


def readings_from_refs(refs) -> list[dict]:
    """Map the engine's ``ReadingsRefs`` onto the reading dicts the rest of the app persists.

    Split out so callers that already hold an engine result (e.g. the ``warm_passage_texts``
    corpus sweep) share one definition of that mapping with :func:`get_daily_readings`.
    """
    return [
        {"book": ref["book"], "book_en": ref["book"],
         **{field: ref[field] for field in _REF_SPAN_FIELDS}}
        for ref in refs or []
    ]


def get_daily_readings(date_obj, church) -> list[dict]:
    """Return the day's readings as dicts, computed offline from ``armenian_lectionary``.

    Returns a list of
    ``{"book", "book_en", "start_chapter", "start_verse", "end_chapter", "end_verse"}`` dicts in
    the order the lectionary should display them, one per sub-reference -- so the single composite
    citation yields two readings, Daniel and Azariah, where the old parser dropped the second.
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

    return readings_from_refs(result.get("ReadingsRefs"))


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
