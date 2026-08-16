"""The rule for putting a stored ``Feast`` under the observance the engine says it is.

``Feast`` is keyed by ``(church, observance_key)`` -- the engine's ordered ``ObservanceIds`` for
the day.  An id is a contract: once armenian-lectionary publishes one it keeps meaning the same
observance, so unlike the display name it does not move when the engine corrects its text.  That
is what this key is for.  It used to be the name, and every correction the engine shipped
silently orphaned rows: the row stopped being reachable while its designation, icon and generated
contexts stayed in the database, and a date lookup minted an empty one beside it.

Three ways a row is placed, in order of confidence:

  * **Its own key.**  Trusted as-is if the engine still produces it.  Nothing to re-derive -- the
    point of the re-key is that this is the steady state.
  * **Its ``sample_date``.**  What the row was created for, asked of the current engine.  This is
    how a row written before ids gets one, and how anything that lost its key recovers.
  * **Its name.**  Legacy, for rows that predate both columns: resolve the stored name (already
    current, or through ``hub/data/feast_name_map.json``, generated once by
    ``scripts/build_feast_name_map.py``), then take the key of a date the engine names that way.

Every function here takes plain model instances and model classes, reading only attributes both
the real and the historical model expose, so the management command and the data migrations run
the identical rule -- the same arrangement ``feast_merge`` has with migration 0062.  Migration
0065 predates the ``observance_key`` column, so writing it is guarded rather than assumed; see
``_can_hold_key``.
"""
import datetime
import functools
import html
import json
import os
import unicodedata

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR

from hub.services.feast_merge import survivor

NAME_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "feast_name_map.json"
)

# Armenian block (U+0530-U+058F) plus the Armenian ligatures in the Alphabetic Presentation
# Forms block (U+FB13-U+FB17), which is where "և" and friends can land.
_ARMENIAN_RANGES = ((0x0530, 0x058F), (0xFB13, 0xFB17))


def normalize_feast_key(name):
    """Fold a feast name to a key that survives how the scrape mangled it.

    Three separate kinds of damage have to collapse to the same key as the clean text:

      * **Jammed components.**  The source packs a position label, the commemoration and an eve
        note into one field separated by ``<br>``.  The retired scraper stripped every tag with no
        replacement, so a stored name reads ``"Eighth day of NativityFeast of Naming..."`` where
        the engine writes ``"Eighth day of Nativity — Feast of Naming..."``.
      * **Un-unescaped entities.**  The scraper never called ``html.unescape``.
      * **Cyrillic homoglyphs.**  The source occasionally types English feast text with Cyrillic
        ``Е``/``о``; the scraper preserved them.

    Dropping every character that is not Latin alphanumeric or Armenian handles all three at once
    -- separators and spacing vanish, and a homoglyph is dropped rather than folded.  That last
    one is only sound because the key is computed the same way on both sides: the map is keyed on
    ``normalize_feast_key(scraped_name)`` and looked up with ``normalize_feast_key(stored_name)``,
    and those are the same string.  Verified over all 429 names in the source corpus: no two
    distinct names collide on a key while disagreeing about the target.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", html.unescape(name))
    kept = [ch for ch in text.lower() if _is_key_char(ch)]
    return "".join(kept)


def _is_key_char(ch):
    """True for Latin alphanumerics and Armenian letters; everything else is noise."""
    if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
        return True
    point = ord(ch)
    return any(low <= point <= high for low, high in _ARMENIAN_RANGES)


@functools.lru_cache(maxsize=2)
def _names_by_date(language):
    """``{date: name}`` for the whole supported range, in one language.

    Cached: the sweep is a second of CPU and its result is fixed for a given engine version, so
    every caller in a process shares one.  ``feast_service`` keeps the inverse (name -> dates) for
    its own reasons; this direction is what a remap needs, because ``sample_date`` asks "what is
    this day called now".
    """
    names = {}
    day = datetime.date(MIN_YEAR, 1, 1)
    end = datetime.date(MAX_YEAR, 12, 31)
    while day <= end:
        result = armenian_lectionary.compute_armenian_lectionary(day, language=language)
        name = (result.get("Liturgical Day") or "").strip()
        if name:
            names[day] = name
        day += datetime.timedelta(days=1)
    return names


def engine_name_for_date(day, language="en"):
    """The name the engine gives this date now, or ``""`` for a date it does not cover."""
    if not day:
        return ""
    if isinstance(day, datetime.datetime):
        day = day.date()
    return _names_by_date(language).get(day, "")


def engine_names(min_year=None, max_year=None):
    """Every distinct English name the engine emits -- the set a stored name must be in.

    A feast is looked up by the name the engine computes for the requested date, so a stored name
    outside this set is unreachable no matter what enrichment hangs off it.
    """
    min_year = MIN_YEAR if min_year is None else min_year
    max_year = MAX_YEAR if max_year is None else max_year
    return {
        name for day, name in _names_by_date("en").items()
        if min_year <= day.year <= max_year
    }


@functools.lru_cache(maxsize=1)
def load_name_map_entries():
    """Return the checked-in artifact's entries, each with its old spellings and current name.

    Missing file is not an error: the map only exists to bridge pre-``sample_date`` rows, and a
    database that never held any (a fresh install, a test) needs nothing from it.
    """
    if not os.path.exists(NAME_MAP_PATH):
        return ()
    with open(NAME_MAP_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return tuple(data.get("entries", ()))


@functools.lru_cache(maxsize=1)
def load_name_map():
    """Return ``{normalized old name: current engine name}``, the form a lookup needs."""
    return {entry["key"]: entry["new"] for entry in load_name_map_entries()}


# Joins the engine's ordered ``ObservanceIds`` into one scalar a column can hold and a unique
# constraint can cover. The engine deliberately imposes no separator; ids are ``[a-z0-9_]`` so
# "+" cannot occur inside one, which makes the join reversible and the key readable in a log.
OBSERVANCE_KEY_SEP = "+"


@functools.lru_cache(maxsize=1)
def _keys_by_date():
    """``{date: observance key}`` for the whole supported range.

    The key is what a ``Feast`` row is identified by.  Unlike the name it does not move when the
    engine corrects its display text -- which is the entire reason it exists, and why the
    name-based machinery in this module is legacy from here on: it bridges rows written before
    the engine served ids, and nothing else.

    Days the engine cannot fully resolve are absent rather than partially keyed; the engine
    already returns ``[]`` rather than a list with a hole in it.
    """
    keys = {}
    day = datetime.date(MIN_YEAR, 1, 1)
    end = datetime.date(MAX_YEAR, 12, 31)
    while day <= end:
        ids = armenian_lectionary.compute_armenian_lectionary(day).get("ObservanceIds") or []
        if ids:
            keys[day] = OBSERVANCE_KEY_SEP.join(ids)
        day += datetime.timedelta(days=1)
    return keys


def observance_key_for_date(day):
    """The observance key for a date, or ``""`` for a date the engine cannot key."""
    if not day:
        return ""
    if isinstance(day, datetime.datetime):
        day = day.date()
    return _keys_by_date().get(day, "")


@functools.lru_cache(maxsize=1)
def _dates_by_key():
    """``{observance key: earliest date the engine gives it}``.

    One date per key is all anything needs: it seeds ``sample_date`` and it is where the display
    name and its Armenian translation are read from.  The earliest is chosen for being
    deterministic, not for meaning anything -- a commemoration recurs, and a movable one has no
    canonical date at all.
    """
    dates = {}
    for day, key in sorted(_keys_by_date().items()):
        dates.setdefault(key, day)
    return dates


def observance_keys():
    """Every observance key the engine can currently produce."""
    return set(_dates_by_key())


def date_for_observance_key(key):
    """A date the engine gives this key, or ``None`` if it never does."""
    return _dates_by_key().get(key)


@functools.lru_cache(maxsize=1)
def _sample_dates():
    """``{name: earliest date the engine emits it}``, for seeding ``Feast.sample_date``.

    Any date the engine names this way would do -- a fixed feast repeats on the same month and
    day, and a movable one has no canonical date at all -- so the earliest is chosen for being
    deterministic rather than for meaning anything.
    """
    dates = {}
    for day, name in sorted(_names_by_date("en").items()):
        dates.setdefault(name, day)
    return dates


def sample_date_for_name(name):
    """A date the engine currently gives this name, or ``None`` if it never does."""
    return _sample_dates().get((name or "").strip())


def resolve_name(feast, reachable, name_for_date=None, name_map=None):
    """Return the name this feast should carry now, or ``None`` if nothing can say.

    ``reachable`` is the set of names the engine currently emits.  Checked in order of confidence:
    the name is already one the engine emits; the row's own ``sample_date`` recomputed; the
    checked-in map, which is the only recourse for rows written before that column existed.
    """
    name = (feast.name or "").strip()
    if name in reachable:
        return name

    if name_for_date is None:
        name_for_date = engine_name_for_date
    sample_date = getattr(feast, "sample_date", None)
    if sample_date:
        by_date = name_for_date(sample_date)
        if by_date:
            return by_date

    if name_map is None:
        name_map = load_name_map()
    return name_map.get(normalize_feast_key(name))


def resolve_key(feast, reachable=None, name_map=None):
    """Return the observance key this feast belongs under, or ``None`` if nothing can say.

    Three routes, in order of confidence:

      1. **The row already has one.**  Trusted as-is if the engine still produces it.  An id is a
         contract -- once published it keeps meaning the same observance -- so unlike a name it
         does not need re-deriving on every engine bump.  That is the whole point of the re-key.
      2. **Its ``sample_date``.**  What the row was named for, asked of the current engine.
      3. **Its name.**  For rows that predate both columns: resolve the name the legacy way
         (already-current, or through ``hub/data/feast_name_map.json``), then take the key of a
         date the engine gives that name.

    Route 3 is the bridge, not the design.  Once every row carries a key, routes 1 and 2 answer
    everything and the name is display data.
    """
    keys = observance_keys()

    stored = (getattr(feast, "observance_key", None) or "").strip()
    if stored and stored in keys:
        return stored

    sample_date = getattr(feast, "sample_date", None)
    if sample_date:
        by_date = observance_key_for_date(sample_date)
        if by_date:
            return by_date

    if reachable is None:
        reachable = engine_names()
    name = resolve_name(feast, reachable, name_map=name_map)
    if name:
        day = sample_date_for_name(name)
        if day:
            return observance_key_for_date(day) or None
    return None


def plan_renames(feasts, reachable=None, name_map=None):
    """Group a church's feasts by the observance each belongs to, and report what that implies.

    Returns ``(groups, unresolved)``.  ``groups`` is a list of ``(observance key, [feast, ...])``
    sorted by key, with each group's feasts in ``id`` order -- which is the order
    ``feast_merge.survivor`` reads, so the oldest row (the scrape-era one holding two years of LLM
    contexts and curated icons) is the one that survives.  ``unresolved`` is every feast nothing
    could place; those are reported and left alone, never deleted.

    A group can hold more than two rows: production accumulated one row per *spelling* of a
    commemoration, and they all collapse onto its single id.
    """
    if reachable is None:
        reachable = engine_names()
    if name_map is None:
        name_map = load_name_map()

    groups = {}
    unresolved = []
    for feast in sorted(feasts, key=lambda f: f.id):
        key = resolve_key(feast, reachable, name_map)
        if key is None:
            unresolved.append(feast)
        else:
            groups.setdefault(key, []).append(feast)

    return sorted(groups.items()), unresolved


def describe(key, group):
    """Classify what applying this group would do, without touching anything.

    ``"unchanged"`` -- one row already carrying this key.  ``"rekey"`` -- one row that has to be
    moved onto it.  ``"merge"`` -- several rows collapsing onto one observance, the only outcome
    that deletes anything.

    A row from before 0066 cannot carry a key, so there is nothing to compare and the verdict
    falls to whether anything else on it is stale (see ``stale_metadata``).
    """
    if len(group) > 1:
        return "merge"
    if not _can_hold_key(group[0]):
        return "unchanged"
    return "unchanged" if group[0].observance_key == key else "rekey"


def apply_group(key, group, Feast, FeastContext):
    """Collapse a group onto one row carrying ``key``.  Returns the surviving feast.

    Delegates the collapse itself to ``feast_merge.survivor``, the rule migration 0062 applied --
    newest active context wins, thumbs are summed across the group, icon and designation are the
    first non-null in ``id`` order.  Reimplementing it here would let the two drift.

    The absorbed rows are deleted **before** the survivor is re-keyed: one of them typically
    already holds ``key`` (the empty row a post-upgrade date lookup minted alongside the stale
    one), and writing it first would collide with ``unique_feast_observance_per_church``.
    """
    keeper = group[0]

    if len(group) > 1:
        merge = survivor(group)
        keeper = merge["keep"]
        absorbed_ids = [f.id for f in merge["absorbed"]]

        # Reparent every context before deleting its old feast; the FK cascades.
        FeastContext.objects.filter(feast_id__in=absorbed_ids).update(feast_id=keeper.id)

        kept_context = merge["context_kept"]
        if kept_context is not None:
            FeastContext.objects.filter(pk=kept_context.pk).update(
                active=True,
                thumbs_up=merge["thumbs_up"],
                thumbs_down=merge["thumbs_down"],
            )
            FeastContext.objects.filter(feast_id=keeper.id).exclude(
                pk=kept_context.pk
            ).update(active=False, thumbs_up=0, thumbs_down=0)

        if not keeper.icon_id and merge["icon_id"]:
            keeper.icon_id = merge["icon_id"]
        if not keeper.designation and merge["designation"]:
            keeper.designation = merge["designation"]

        Feast.objects.filter(id__in=absorbed_ids).delete()

    if _can_hold_key(keeper):
        keeper.observance_key = key
    return keeper


def _can_hold_key(feast):
    """Whether this instance's model has the ``observance_key`` column yet.

    Migration 0065 applies this module through a historical model from before 0066 added it. The
    identity is written where it can be; 0067 fills it in for every row a moment later.
    """
    return any(f.name == "observance_key" for f in feast._meta.fields)


def stale_metadata(feast, key):
    """Name the fields that would change if this row were brought up to date.  Mutates nothing.

    ``observance_key`` is the identity; everything else on the row is *derived from it* and is
    brought along whenever the engine's answer moves.

    The key itself is reported only where the row can hold one.  Migration 0065 runs this against
    a historical model from before 0066 added the column, so the field is checked for rather than
    assumed -- the same "read only what both models expose" rule the module docstring sets out.
    A row that cannot hold a key still gets its names and date refreshed, which is all 0065 ever
    did.

      * ``name`` and ``name_hy``, the display text in both languages.  These are exactly what used
        to be the key, and exactly what an engine release corrects -- which is why they are no
        longer the key.
      * ``sample_date``, the date the key and the names are read from.
    """
    day = date_for_observance_key(key)
    stale = []
    if _can_hold_key(feast) and (feast.observance_key or "") != key:
        stale.append("observance_key")
    if feast.name != engine_name_for_date(day):
        stale.append("name")
    if (feast.i18n or {}).get("name_hy") != _target_hy(key):
        stale.append("name_hy")
    if feast.sample_date is None or observance_key_for_date(feast.sample_date) != key:
        stale.append("sample_date")
    return stale


def refresh_metadata(feast, key):
    """Apply what ``stale_metadata`` reports, in memory.  The caller saves."""
    day = date_for_observance_key(key)
    if _can_hold_key(feast):
        feast.observance_key = key
    feast.name = engine_name_for_date(day)
    set_translation(feast, _target_hy(key))
    if feast.sample_date is None or observance_key_for_date(feast.sample_date) != key:
        feast.sample_date = day
    return feast


def _target_hy(key):
    """The engine's Armenian name for an observance, or ``None`` where it has none."""
    return engine_name_for_date(date_for_observance_key(key), language="hy") or None


def set_translation(feast, name_hy):
    """Write the Armenian name into the modeltrans ``i18n`` column directly.

    The historical models a migration sees carry the ``i18n`` column but not modeltrans's
    ``name_hy`` descriptor, so going through the JSON is what lets the command and the migration
    share this code.  Returns True if anything changed.
    """
    current = dict(feast.i18n or {})
    if current.get("name_hy") == name_hy:
        return False
    if name_hy:
        current["name_hy"] = name_hy
    else:
        current.pop("name_hy", None)
    feast.i18n = current
    return True
