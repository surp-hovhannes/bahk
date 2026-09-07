"""Compute the day's commemorations from the offline ``armenian_lectionary`` engine.

Reads the engine's ``"Observances"`` array in both ``en`` and ``hy``.  A liturgical day is a
list of observances, not one name: the engine joins them into ``"Liturgical Day"`` for display,
but serves the components separately, each with the marks that say what it is.  Only the ones
marked ``is_comm`` -- commemorating a person or an event, rather than merely locating the day in
the calendar -- become feasts here.
"""
import functools
import logging
from datetime import date, datetime, timedelta

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR

from hub.utils import SUPPORTED_CHURCHES

logger = logging.getLogger(__name__)


def get_feast_for_date(date_obj, church) -> list[dict] | None:
    """Return the day's commemorations, computed offline from ``armenian_lectionary``.

    One dict per commemoration, in the order the engine serves them, with ``"observance_id"``,
    ``"name"``, ``"name_en"`` and ``"name_hy"`` keys.  ``observance_id`` is what a ``Feast`` row
    is keyed by: a published catalog id keeps meaning the same observance across engine releases,
    while the display text gets corrected.

    Most days have none.  Of the 9,861 days the engine supports, 5,070 carry no commemoration at
    all (the weekly Wednesday and Friday fasts alone are 1,334), 4,606 carry one and 185 carry
    two -- so an empty list is the single most common answer, and means "nothing to show today".

    ``None`` is a different fact: no answer at all, for an unsupported church, a date outside the
    validated year window, or a day the engine could not resolve.  Callers must not collapse the
    two -- a broken install answers ``None`` for every day, and reading that as "no commemoration"
    would quietly serve an empty calendar.
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
    observances_en = result_en.get("Observances") or []
    if not observances_en:
        # The engine resolves its components all or nothing, so an empty array never means "a day
        # with nothing on it" -- it means at least one component had no catalog entry, and on an
        # install missing the catalog every day answers this way.
        logger.warning(
            "Engine did not resolve observances for %s; no feast returned.", date_obj)
        return None

    result_hy = armenian_lectionary.compute_armenian_lectionary(date_obj, language="hy")
    # Paired by id, not by position: the ids are language-independent by construction, so this
    # states the join the engine guarantees instead of assuming the two lists line up.
    names_hy = {o["id"]: o["name"] for o in result_hy.get("Observances") or []}

    return [
        {
            "observance_id": observance["id"],
            "name": observance["name"],
            "name_en": observance["name"],
            "name_hy": names_hy.get(observance["id"], ""),
        }
        # Marked per observance and human-reviewed, not inferred from the text. Shape cannot
        # substitute: "Sixth Sunday of Great Lent: Sunday of the Advent" and "Sixth day of
        # Nativity" read identically and answer oppositely. is_fast is deliberately not consulted
        # -- the two marks are independent, and the six ids carrying both include Great Friday.
        for observance in observances_en if observance["is_comm"]
    ]


@functools.lru_cache(maxsize=1)
def _dates_by_name():
    """Map each English feast name to every date in the supported range the engine gives it.

    Feasts are keyed by commemoration rather than by date, so a Feast row has no date of its own.
    One thing still needs dates: the reference-data matcher in ``llm_service``, which boosts its
    confidence when a candidate in ``data/feasts.json`` falls on the same month and day.

    Cache invalidation used to be the other caller. It no longer is: invalidation bumps a per-church
    generation folded into the cache key, which orphans every entry at once without enumerating the
    days a feast is served on.

    Keyed on the name of a single OBSERVANCE, not on the day's joined ``"Liturgical Day"``.  A
    ``Feast`` holds one component now, so the joined string would miss every day that names more
    than one thing -- and it would miss it silently, returning no date rather than raising, which
    is how the matcher would have quietly degraded to name-only confidence.

    Cached: it sweeps the engine's whole supported range, which costs a couple of seconds, and
    that result is fixed for a given engine version. Sweeping is affordable precisely because it
    happens at most once per process.
    """
    dates = {}
    day = date(MIN_YEAR, 1, 1)
    end = date(MAX_YEAR, 12, 31)
    while day <= end:
        result = armenian_lectionary.compute_armenian_lectionary(day)
        for observance in result.get("Observances") or []:
            name = (observance.get("name") or "").strip()
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
