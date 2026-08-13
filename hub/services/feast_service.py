"""Compute the feast/fast name of the day from the offline ``armenian_lectionary`` engine.

Reads the engine's ``"Liturgical Day"`` field in ``en`` and ``hy``. ``name_hy`` is left ``None``
when the engine has no Armenian form, so callers fall back to English via ``name_i18n``.
"""
import logging
from datetime import datetime

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
    if not name_en:
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
