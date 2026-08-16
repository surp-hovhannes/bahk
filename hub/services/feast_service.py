"""Compute the feast/fast name of the day from the offline ``armenian_lectionary`` engine.

Reads the engine's ``"Liturgical Day"`` field in both ``en`` and ``hy``.
"""
import functools
import logging
from datetime import date, datetime, timedelta

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR

from hub.utils import SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)


def get_feast_for_date(date_obj, church) -> dict | None:
    """Return the day's observance, computed offline from ``armenian_lectionary``.

    Returns a dict with ``"observance_key"``, ``"name"``, ``"name_en"`` and ``"name_hy"`` keys, or
    ``None`` if there is no feast to record.  Returns ``None`` for unsupported churches or dates
    outside the validated year window.

    ``observance_key`` is what a ``Feast`` row is keyed by: the engine's ordered ``ObservanceIds``
    joined into one scalar.  It is empty on a day the engine cannot fully resolve, which inside
    the supported range does not happen -- callers fall back to the name rather than assume.
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

    # Imported here rather than at module scope: feast_rename imports this module's sibling
    # feast_merge, and a top-level import would close the loop.
    from hub.services.feast_rename import OBSERVANCE_KEY_SEP

    return {
        "observance_key": OBSERVANCE_KEY_SEP.join(result_en.get("ObservanceIds") or ""),
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
    day = date(MIN_YEAR, 1, 1)
    end = date(MAX_YEAR, 12, 31)
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
