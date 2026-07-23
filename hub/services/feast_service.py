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
import logging
from datetime import datetime

import armenian_lectionary
from django.conf import settings

from hub.utils import SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)

# The engine computes any date, but readings/names are only validated for 2001-2027 (the range
# guard lives in armenian_lectionary/app.py).  Mirror the lectionary service's window so feast
# names are only served where they are validated.  Overridable via settings.
LECTIONARY_MIN_YEAR = getattr(settings, "LECTIONARY_MIN_YEAR", 2001)
LECTIONARY_MAX_YEAR = getattr(settings, "LECTIONARY_MAX_YEAR", 2027)

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
