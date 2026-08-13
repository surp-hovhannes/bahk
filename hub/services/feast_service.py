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


def _fit_to_storage(name: str, date_obj) -> str:
    """Clamp ``name`` to what ``Feast.name`` can hold, reading the limit from the model.

    The column is wide enough for every name the engine produces (the longest is 289
    characters -- the Twelve Holy Doctors, whose name enumerates all twelve), so this
    should never fire.  It exists because the failure it prevents is disproportionate: on
    PostgreSQL an over-long value raises ``DataError``, which makes the API degrade to "no
    feast" for that day and aborts a range import partway through, while SQLite (the test
    DB) accepts it silently so no test would catch the regression.

    The limit is read from the field rather than hard-coded so it cannot drift away from
    the column after a future migration.
    """
    from hub.models import Feast          # local import: avoids a circular import at load

    max_length = Feast._meta.get_field("name").max_length
    if len(name) <= max_length:
        return name
    logger.error(
        "Lectionary feast name for %s is %d characters, over the Feast.name limit of %d; "
        "truncating to store it. Widen the column instead -- the full name is correct.",
        date_obj, len(name), max_length,
    )
    return name[:max_length]


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

    name_en = _fit_to_storage(name_en, date_obj)

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
