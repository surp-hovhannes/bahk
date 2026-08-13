"""Compute the feast/fast name of the day from the offline ``armenian_lectionary`` engine.

This replaces the previous sacredtradition.am scraping with an in-process, offline call to
:func:`armenian_lectionary.compute_armenian_lectionary`.  The engine returns the commemoration in
its ``"Liturgical Day"`` field; as of the engine's feast-name accuracy contract this name is locked
against the same authoritative ground truth the scrape used (100% match across 2001-2026), so it is
a drop-in replacement for the English feast name.

As of ``armenian_lectionary`` 1.2, the engine also serves Armenian (``"hy"``) feast names (baked
into the package offline), so ``name_hy`` is now populated here too — the last thing feast names
needed the live scrape for.  The engine leaves any feast without a known Armenian form in English;
we treat that as "no Armenian translation" (``name_hy = None``) so those names fall back to English
via ``name_i18n`` and can be upgraded later when the engine gains the translation.
"""
import functools
import logging
from datetime import date, datetime, timedelta

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR
from django.conf import settings

from hub.utils import SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)

# Feast names are validated only over the engine's supported range.  As of armenian-lectionary
# 1.3.0 that guard lives in the engine itself, which raises ValueError outside the range rather
# than returning the placeholder text this module would otherwise have to recognise -- so check
# before calling.  Default to the engine's own bounds so the two cannot drift; the settings
# override still allows narrowing the window (or widening it alongside a new engine release).
LECTIONARY_MIN_YEAR = getattr(settings, "LECTIONARY_MIN_YEAR", MIN_YEAR)
LECTIONARY_MAX_YEAR = getattr(settings, "LECTIONARY_MAX_YEAR", MAX_YEAR)

# The engine returns a concrete, source-matched name for every day.  A handful of internal
# placeholders (e.g. "(commemoration)", "(movable ordinary-time reading)") and the pre-validated-
# table sentinel are not real commemorations; treat them as "no feast" so we don't persist them.
_NON_FEAST_MARKERS = (
    "(commemoration)",
    "(movable ordinary-time reading)",
    "day not yet in validated table",
)


def get_feast_for_date(date_obj, church) -> dict | None:
    """Return the day's feast name, computed offline from ``armenian_lectionary``.

    Returns a dict with ``"name"``, ``"name_en"`` and ``"name_hy"`` keys, or ``None`` if there is
    no feast to record.  ``name_hy`` is the engine's Armenian feast name, or ``None`` when the
    engine has no Armenian form for it (it leaves those in English, which the API already reaches
    via ``name_i18n`` fallback).  Returns ``None`` for unsupported churches or dates outside the
    validated year window.
    """
    if church not in SUPPORTED_CHURCHES:
        logger.error(
            "Feast names only set up for the following churches: %r. %s not supported.",
            SUPPORTED_CHURCHES, church,
        )
        return None

    # The engine does date arithmetic/comparisons; callers may pass datetime objects.
    if isinstance(date_obj, datetime):
        date_obj = date_obj.date()

    if not (LECTIONARY_MIN_YEAR <= date_obj.year <= LECTIONARY_MAX_YEAR):
        logger.warning(
            "Date %s is outside the validated lectionary range %d-%d; no feast returned.",
            date_obj, LECTIONARY_MIN_YEAR, LECTIONARY_MAX_YEAR,
        )
        return None

    result_en = armenian_lectionary.compute_armenian_lectionary(date_obj, language="en")
    name_en = (result_en.get("Liturgical Day") or "").strip()
    if not name_en or any(marker in name_en for marker in _NON_FEAST_MARKERS):
        return None

    result_hy = armenian_lectionary.compute_armenian_lectionary(date_obj, language="hy")
    name_hy = (result_hy.get("Liturgical Day") or "").strip()
    # The engine leaves untranslated feast names in English.  Record only a genuine Armenian
    # translation so untranslated names keep falling back to English (and can be upgraded later).
    if not name_hy or name_hy == name_en:
        name_hy = None

    return {
        "name": name_en,
        "name_en": name_en,
        "name_hy": name_hy,
    }


@functools.lru_cache(maxsize=1)
def _dates_by_name():
    """Map each English feast name to every date in the supported range the engine gives it.

    Feasts are keyed by commemoration rather than by date, so a Feast row has no date of its own.
    Two things still need dates:

      * cache invalidation, which has to clear the API entries for every day a feast is served on;
      * the reference-data matcher in ``llm_service``, which boosts its confidence when a
        candidate in ``data/feasts.json`` falls on the same month and day.

    Cached: it sweeps the engine's whole supported range, which costs a couple of seconds, and
    that result is fixed for a given engine version. Sweeping is affordable precisely because it
    happens at most once per process.
    """
    dates = {}
    day = date(LECTIONARY_MIN_YEAR, 1, 1)
    end = date(LECTIONARY_MAX_YEAR, 12, 31)
    while day <= end:
        name = (armenian_lectionary.compute_armenian_lectionary(day)
                .get("Liturgical Day") or "").strip()
        if name:
            dates.setdefault(name, []).append(day)
        day += timedelta(days=1)
    return dates


def dates_for_feast_name(name):
    """Return every date in the supported range the engine gives this feast name."""
    if not name:
        return []
    return _dates_by_name().get(name.strip(), [])


def representative_date_for_feast_name(name):
    """Return one date the engine gives this feast name, or ``None`` if it never does.

    For a fixed feast every occurrence shares a month and day, so the earliest is as good as any.
    For a movable one the date shifts with Easter and no single date is right -- but those never
    matched a fixed month/day entry in the reference file anyway, so callers relying on the
    month/day were never getting work out of them.
    """
    occurrences = dates_for_feast_name(name)
    return occurrences[0] if occurrences else None
