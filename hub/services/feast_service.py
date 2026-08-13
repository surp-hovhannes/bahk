"""Compute the feast/fast name of the day from the offline ``armenian_lectionary`` engine.

Reads the engine's ``"Liturgical Day"`` field in both ``en`` and ``hy``.
"""
import logging
from datetime import datetime

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR

from hub.utils import SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)


def get_feast_for_date(date_obj, church) -> dict | None:
    """Return the day's feast name, computed offline from ``armenian_lectionary``.

    Returns a dict with ``"name"``, ``"name_en"`` and ``"name_hy"`` keys, or ``None`` if there is
    no feast to record.  Returns ``None`` for unsupported churches or dates outside the validated
    year window.
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

    # As of armenian-lectionary 1.3.0 this range guard lives in the engine itself, which
    # raises ValueError outside MIN_YEAR-MAX_YEAR rather than returning placeholder text --
    # so check first and skip the call entirely.
    if not (MIN_YEAR <= date_obj.year <= MAX_YEAR):
        logger.warning(
            "Date %s is outside the validated lectionary range %d-%d; no feast returned.",
            date_obj, MIN_YEAR, MAX_YEAR,
        )
        return None

    result_en = armenian_lectionary.compute_armenian_lectionary(date_obj, language="en")
    name_en = (result_en.get("Liturgical Day") or "").strip()
    if not name_en:
        return None

    result_hy = armenian_lectionary.compute_armenian_lectionary(date_obj, language="hy")
    name_hy = (result_hy.get("Liturgical Day") or "").strip()

    return {
        "name": name_en,
        "name_en": name_en,
        "name_hy": name_hy,
    }
