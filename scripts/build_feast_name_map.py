"""DEV-ONLY: build the old-feast-name -> current-engine-name map bahk ships in hub/data/.

Feasts are keyed by ``(church, name)`` and the name is recomputed per request from the engine, so
a stored name the engine no longer emits is unreachable -- see ``hub/services/feast_rename.py``.
Rows created from now on record a ``sample_date`` and can always be recomputed from it, but the
rows already in production cannot: they were written before that column existed, by two sources
that are both gone.

    1. **The retired sacredtradition.am scrape** (through 2026-07-21).  Its text is recoverable
       from the lectionary repo's reference cache -- ``dev/reference_data/*.json`` holds the same
       ``dname`` field the scraper read, for every day of the supported range.
    2. **Engine releases 1.1.x/1.2.x**, which minted rows between the scrape's retirement and the
       1.3.0 upgrade.  Each is on PyPI, so its names are recoverable by installing it and sweeping.

Both are joined to the current engine the same way: **by date**.  Whatever an old source called a
day, the current engine's name for that same day is what the row should be called now.  Where one
old name spans dates the current engine now calls different things -- the source glued some
commemorations together in some years and not others -- the date-count majority wins and the
entry is flagged so a human can read the rejected alternatives.

This is a one-time artifact.  The recurring path after any future engine upgrade is
``manage.py remap_feast_names``, which needs neither this script nor an old engine version.

Usage (from the repo root, with armenian-lectionary 1.3.0+ installed):

    python scripts/build_feast_name_map.py
    python scripts/build_feast_name_map.py --reference-data ../armenian_lectionary/dev/reference_data
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hub.services.feast_rename import normalize_feast_key  # noqa: E402

DEFAULT_REFERENCE_DATA = os.path.join(
    os.path.dirname(REPO_ROOT), "armenian_lectionary", "dev", "reference_data"
)
DEFAULT_OUT = os.path.join(REPO_ROOT, "hub", "data", "feast_name_map.json")

# Every release that could have written a Feast row: the scrape was retired on 1.1.0 and 1.3.0 is
# the target, so these are the versions in between. 1.0.x never shipped in bahk.
DEFAULT_VERSIONS = "1.1.0,1.1.1,1.2.0,1.2.1,1.2.2,1.2.3"

CACHE_SOURCE = "sacredtradition-cache"

# Engine internals that are not commemorations. 1.1.x/1.2.x emitted these on days their table did
# not cover; ``feast_service`` filtered them at the time, so no row should carry one -- but a
# placeholder spans unrelated days, so letting one into the map would invent a rename between
# feasts that have nothing to do with each other. Excluded as both source and target.
PLACEHOLDERS = frozenset({
    "(commemoration)",
    "(movable ordinary-time reading)",
    "day not yet in validated table",
})

# Sweeps run in a throwaway venv against an arbitrary old release, so this asks for nothing beyond
# what 1.1.0 already had: a positional date and the "Liturgical Day" key. MIN_YEAR/MAX_YEAR were
# only exported later, and 1.3.0 raises outside the range where earlier releases returned
# placeholder text -- hence the fallback bounds and the per-day guard.
_SWEEP = """
import datetime, json, sys
import armenian_lectionary as al
out = {}
day = datetime.date(getattr(al, "MIN_YEAR", 2001), 1, 1)
end = datetime.date(getattr(al, "MAX_YEAR", 2027), 12, 31)
while day <= end:
    try:
        name = (al.compute_armenian_lectionary(day).get("Liturgical Day") or "").strip()
    except Exception:
        name = ""
    if name:
        out[day.isoformat()] = name
    day += datetime.timedelta(days=1)
json.dump(out, sys.stdout)
"""


def sweep_installed():
    """Return ``({date: name}, version)`` for the engine installed in this interpreter."""
    import armenian_lectionary as al

    names = {}
    day = datetime.date(al.MIN_YEAR, 1, 1)
    end = datetime.date(al.MAX_YEAR, 12, 31)
    while day <= end:
        name = (al.compute_armenian_lectionary(day).get("Liturgical Day") or "").strip()
        if name:
            names[day] = name
        day += datetime.timedelta(days=1)
    return names, _installed_version()


def _installed_version():
    from importlib.metadata import version

    return version("armenian-lectionary")


def sweep_version(version, venv_python):
    """Install one release into the scratch venv and return its ``{date: name}``."""
    subprocess.run(
        [venv_python, "-m", "pip", "install", "--quiet", f"armenian-lectionary=={version}"],
        check=True,
    )
    result = subprocess.run(
        [venv_python, "-c", _SWEEP], check=True, capture_output=True, text=True
    )
    return {
        datetime.date.fromisoformat(day): name
        for day, name in json.loads(result.stdout).items()
    }


def sweep_reference_cache(directory):
    """Return ``{date: feast text}`` from the lectionary repo's sacredtradition cache.

    This is the same string the retired scraper read out of ``<div class=dname>``, modulo the
    normalization ``normalize_feast_key`` exists to undo.
    """
    names = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            day = json.load(fh)
        if day.get("feast"):
            names[datetime.date.fromisoformat(day["date"])] = day["feast"]
    return names


def build_entries(sources, target):
    """Join every old source to ``target`` by date and collapse to one entry per old name.

    ``sources`` is ``{source label: {date: old name}}``.  Names the current engine still emits are
    dropped: ``resolve_name`` returns those before it ever consults the map, so an entry for one
    would be dead weight in a file that is read on every remap.
    """
    votes = {}  # old name -> Counter of current names
    dates = {}  # (old name, current name) -> earliest date supporting it
    labels = {}  # old name -> set of source labels

    for label, by_date in sources.items():
        for day, old in by_date.items():
            current = target.get(day)
            if not current or old in PLACEHOLDERS or current in PLACEHOLDERS:
                continue
            votes.setdefault(old, Counter())[current] += 1
            labels.setdefault(old, set()).add(label)
            key = (old, current)
            if day < dates.get(key, datetime.date.max):
                dates[key] = day

    reachable = set(target.values())
    entries = []
    for old, counted in votes.items():
        if old in reachable:
            continue
        ranked = _rank(old, counted)
        current = ranked[0][0]
        entries.append({
            "key": normalize_feast_key(old),
            "old": old,
            "new": current,
            # A date the CURRENT engine emits ``new`` on, so it can seed Feast.sample_date.
            "sample_date": dates[(old, current)].isoformat(),
            "dates": counted.total(),
            "sources": sorted(labels[old]),
            "ambiguous": len(ranked) > 1,
            "rejected": [name for name, _ in ranked[1:]],
        })

    return sorted(entries, key=lambda e: e["key"])


def dedupe_by_key(entries):
    """Collapse entries that share a key, which is the map's actual lookup unit.

    Most duplicates are one name in its separated and its jammed spelling -- 1.1.0 ran the
    components together exactly as the scraper did, 1.1.1 onward joined them with an em dash --
    and the key exists precisely so those are one thing.  Run this only after
    ``assert_no_conflicting_keys``, so the check still compares the variants rather than a
    pre-merged single answer.  The longest raw spelling is kept as ``old`` because it is the
    readable one; every variant's sources are unioned.
    """
    merged = {}
    for entry in entries:
        seen = merged.get(entry["key"])
        if seen is None:
            merged[entry["key"]] = dict(entry, variants=[entry["old"]])
            continue
        seen["variants"].append(entry["old"])
        seen["dates"] += entry["dates"]
        seen["sources"] = sorted(set(seen["sources"]) | set(entry["sources"]))
        seen["ambiguous"] = seen["ambiguous"] or entry["ambiguous"]
        seen["rejected"] = sorted(set(seen["rejected"]) | set(entry["rejected"]))
        seen["sample_date"] = min(seen["sample_date"], entry["sample_date"])
        if len(entry["old"]) > len(seen["old"]):
            seen["old"] = entry["old"]

    for entry in merged.values():
        entry["variants"] = sorted(entry["variants"])
    return sorted(merged.values(), key=lambda e: e["key"])


def _rank(old, counted):
    """Order an old name's candidate targets best-first.

    Date count decides, with one candidate promoted ahead of it: a target that folds to the same
    key as the old name is the same text differing only in what the key erases -- separators,
    spelling, homoglyphs -- so it is the rename, not a coincidence.  This is what settles the
    calendar-position labels.  1.1.0 jammed a day's components together exactly as the scraper
    did, so ``"Fourth Sunday after AssumptionEve of Fast of Exaltation of Holy Cross"`` has to land
    on the *Fourth* Sunday's separated form; 1.3.0 regenerates that label per year, so several
    ordinals are in the running and the raw counts are near enough to be arbitrary.
    """
    old_key = normalize_feast_key(old)
    return sorted(
        counted.most_common(),
        key=lambda pair: (normalize_feast_key(pair[0]) != old_key, -pair[1], pair[0]),
    )


def assert_no_conflicting_keys(entries):
    """Fail loudly if two old names fold to one key while disagreeing about the target.

    ``normalize_feast_key`` is aggressive on purpose -- it has to erase separators, entities and
    homoglyphs.  Aggressive folding can in principle merge two genuinely different names, and the
    only safe moment to find out is here, not against production rows.
    """
    by_key = {}
    for entry in entries:
        by_key.setdefault(entry["key"], set()).add(entry["new"])
    conflicts = {key: names for key, names in by_key.items() if len(names) > 1}
    if conflicts:
        for key, names in conflicts.items():
            print(f"  COLLISION {key[:60]!r} -> {sorted(names)}", file=sys.stderr)
        raise SystemExit(
            f"{len(conflicts)} normalized key(s) map to more than one name; the map is unsafe."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference-data", default=DEFAULT_REFERENCE_DATA,
                        help="armenian_lectionary dev/reference_data directory (the scrape cache)")
    parser.add_argument("--versions", default=DEFAULT_VERSIONS,
                        help="comma-separated old engine releases to sweep from PyPI")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the map")
    args = parser.parse_args(argv)

    target, target_version = sweep_installed()
    print(f"target engine {target_version}: {len(set(target.values()))} distinct names "
          f"over {len(target)} days")

    sources = {}
    if os.path.isdir(args.reference_data):
        sources[CACHE_SOURCE] = sweep_reference_cache(args.reference_data)
        print(f"{CACHE_SOURCE}: {len(set(sources[CACHE_SOURCE].values()))} distinct names")
    else:
        print(f"WARNING: no reference cache at {args.reference_data}; skipping the scrape era.",
              file=sys.stderr)

    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    if versions:
        with tempfile.TemporaryDirectory() as work:
            subprocess.run([sys.executable, "-m", "venv", work], check=True)
            venv_python = os.path.join(work, "bin", "python")
            for version in versions:
                sources[version] = sweep_version(version, venv_python)
                print(f"engine {version}: {len(set(sources[version].values()))} distinct names")

    entries = build_entries(sources, target)
    assert_no_conflicting_keys(entries)
    entries = dedupe_by_key(entries)

    ambiguous = [e for e in entries if e["ambiguous"]]
    print(f"\n{len(entries)} name(s) need remapping; {len(ambiguous)} resolved by date-count "
          f"majority:")
    for entry in ambiguous:
        print(f"  {entry['old'][:70]!r}\n    -> {entry['new'][:70]!r} ({entry['dates']} dates)")
        for rejected in entry["rejected"]:
            print(f"       rejected: {rejected[:70]!r}")

    payload = {
        "target_engine_version": target_version,
        "sources": sorted(sources),
        "generated_by": "scripts/build_feast_name_map.py",
        "entries": entries,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
