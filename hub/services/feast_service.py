"""Compute the feast/fast name of the day from the offline ``armenian_lectionary`` engine.

This replaces the previous sacredtradition.am scraping (``hub.utils.scrape_feast``) with an
in-process, offline call to :func:`armenian_lectionary.compute_armenian_lectionary`.  The engine
returns the commemoration in its ``"Liturgical Day"`` field; as of the engine's 1.1.0 feast-name
accuracy contract this name is locked against the same authoritative ground truth the scrape used
(100% match across 2001-2026), so it is a near drop-in replacement for the English feast name.

The engine is English-only, so ``name_hy`` is ``None`` here.  Downstream persistence
(``get_or_create_feast_for_date`` / ``import_feasts``) only fills a missing Armenian translation,
so existing feasts keep any Armenian name they already have, and new feasts fall back to the
English name via ``name_i18n``.
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

    Drop-in replacement for ``hub.utils.scrape_feast``: returns a dict with ``"name"``,
    ``"name_en"`` and ``"name_hy"`` keys, or ``None`` if there is no feast to record.
    The engine is English-only, so ``name_hy`` is always ``None``.  Returns ``None`` for
    unsupported churches or dates outside the validated year window.
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

    result = armenian_lectionary.compute_armenian_lectionary(date_obj)

    name_en = (result.get("Liturgical Day") or "").strip()
    if not name_en or any(marker in name_en for marker in _NON_FEAST_MARKERS):
        return None

    return {
        "name": name_en,
        "name_en": name_en,
        "name_hy": None,
    }
