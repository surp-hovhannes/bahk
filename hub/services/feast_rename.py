"""The rule for moving a stored ``Feast`` name onto the name the engine emits today.

``Feast`` is keyed by ``(church, name)`` and the name is recomputed per request from
``armenian_lectionary``, so the name is not stored data a date points at -- it is the lookup key.
A stored name the engine no longer emits is therefore unreachable: its designation, icon and
generated contexts are still in the database and nothing will ever find them again, and nothing
errors.  ``audit_feast_duplicates`` reports that state; this module repairs it.

Two ways a name is repaired, in order of confidence:

  * **By date.**  ``Feast.sample_date`` records a date the engine gave this name when the row was
    created, so the current name for that date is what the row should be called now.  This is the
    path every future engine upgrade takes, and it needs no artifact.
  * **By the checked-in map.**  Rows that predate ``sample_date`` -- everything the retired
    sacredtradition.am scrape wrote, plus what engines 1.1.x/1.2.x minted -- have no date to
    recompute from.  ``hub/data/feast_name_map.json`` supplies old-name -> current-name for those,
    generated once by ``scripts/build_feast_name_map.py`` from the same date join.

Every function here takes plain model instances and model classes, reading only attributes both
the real and the historical model expose, so the management command and the data migration run
the identical rule -- the same arrangement ``feast_merge`` has with migration 0062.
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


def plan_renames(feasts, reachable, name_for_date=None, name_map=None):
    """Group a church's feasts by the name each should carry, and report what that implies.

    Returns ``(groups, unmapped)``.  ``groups`` is a list of ``(target_name, [feast, ...])`` sorted
    by target name, with each group's feasts in ``id`` order -- which is the order
    ``feast_merge.survivor`` reads, so the oldest row (the scrape-era one holding two years of LLM
    contexts and curated icons) is the one that survives.  ``unmapped`` is every feast nothing
    could resolve; those are reported and left alone, never deleted.

    A group can hold more than two rows.  The source glued some commemorations together on
    different years -- ``"The Hermit Saints Anton"`` and the full four-saint form both resolve to
    ``"The Hermits Sts. Anton, Tryphon, Barsauma and Onuphrius"`` -- so collapsing per target name
    rather than pairwise is what keeps that correct.
    """
    if name_map is None:
        name_map = load_name_map()

    groups = {}
    unmapped = []
    for feast in sorted(feasts, key=lambda f: f.id):
        target = resolve_name(feast, reachable, name_for_date, name_map)
        if target is None:
            unmapped.append(feast)
        else:
            groups.setdefault(target, []).append(feast)

    return sorted(groups.items()), unmapped


def describe(target_name, group):
    """Classify what applying this group would do, without touching anything.

    ``"unchanged"`` -- one row already named this.  ``"rename"`` -- one row under a stale name.
    ``"merge"`` -- several rows collapsing onto one, which is the case worth reading closely
    because it is the only one that deletes anything.
    """
    if len(group) > 1:
        return "merge"
    return "unchanged" if group[0].name == target_name else "rename"


def apply_group(target_name, group, Feast, FeastContext):
    """Collapse a group onto one row carrying ``target_name``.  Returns the surviving feast.

    Delegates the collapse itself to ``feast_merge.survivor``, the rule migration 0062 applied --
    newest active context wins, thumbs are summed across the group, icon and designation are the
    first non-null in ``id`` order.  Reimplementing it here would let the two drift.

    The absorbed rows are deleted **before** the survivor is renamed: one of them is typically
    already named ``target_name`` (the empty row a post-upgrade date lookup minted alongside the
    stale one), and renaming first would collide with ``unique_feast_per_church``.
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

    keeper.name = target_name
    return keeper


def stale_metadata(feast, target_name):
    """Name the fields that would change if this row were brought up to date.  Mutates nothing.

    Beyond the name itself, two things are refreshed from the engine on every remap:

      * ``name_hy``, because the engine is the authority on both languages and corrects them
        across releases -- and because the rows the retired scrape wrote took their Armenian from
        a language code sacredtradition.am does not define, so those values mean nothing.
      * ``sample_date``, because a row whose recorded date no longer produces its name cannot be
        followed through the *next* rename, which is the one thing the column exists for.
    """
    stale = []
    if feast.name != target_name:
        stale.append("name")
    if (feast.i18n or {}).get("name_hy") != _target_hy(target_name):
        stale.append("name_hy")
    if feast.sample_date is None or engine_name_for_date(feast.sample_date) != target_name:
        stale.append("sample_date")
    return stale


def refresh_metadata(feast, target_name):
    """Apply what ``stale_metadata`` reports, in memory.  The caller saves."""
    feast.name = target_name
    set_translation(feast, _target_hy(target_name))
    if feast.sample_date is None or engine_name_for_date(feast.sample_date) != target_name:
        feast.sample_date = sample_date_for_name(target_name)
    return feast


def _target_hy(target_name):
    """The engine's Armenian name for a commemoration, or ``None`` where it has none."""
    return engine_name_for_date(sample_date_for_name(target_name), language="hy") or None


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
