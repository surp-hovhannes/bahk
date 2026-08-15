# Upgrading armenian-lectionary without stranding feast enrichment

`Feast` is unique on `(church, name)` and the name is recomputed from the engine on every
request. The name is therefore the **lookup key**, not stored data a date points at — and that
makes an engine release that renames a commemoration a data-loss event that reports nothing:

- the stored row keeps the old name and no date lookup ever reaches it again;
- its AI `designation`, matched `icon` and generated `FeastContext`s are still in the database,
  unreachable;
- the next request for one of its dates creates a fresh, empty row under the new name, and the
  LLM and icon-matching tasks run again from scratch.

`Feast.sample_date` exists to make that recoverable. It records one date the engine gave the row
its name, written at creation, so the rename can be followed from the row itself: recompute the
name for that date, and that is where the row belongs now.

## The procedure

Run this whenever the `armenian-lectionary` pin moves.

```bash
# 1. Bump the floor in requirements.txt, install, and confirm the map still agrees with it.
python manage.py test hub.tests.test_feast_rename --settings=tests.test_settings

# 2. Report. Read it -- "merge" is the only outcome that deletes a row.
python manage.py remap_feast_names

# 3. Apply.
python manage.py remap_feast_names --apply

# 4. Confirm: no unreachable names, no pending renames, no rows without a date.
python manage.py audit_feast_duplicates --verbose
```

Step 1 is not optional. `hub/data/feast_name_map.json` names 1.3.0 commemorations; if a release
renames one of them, `test_feast_rename.NameMapTests` fails and the map has to be regenerated
before the rest of the procedure means anything.

`remap_feast_names` is idempotent and safe to re-run — a second pass reports every row unchanged.
It writes nothing without `--apply`.

## What each step does

`remap_feast_names` resolves every row to the name it should carry now, in order of confidence:

1. **The name is already one the engine emits.** Nothing to do, though `name_hy` and
   `sample_date` are still refreshed from the engine if either has drifted.
2. **The row's `sample_date` recomputed.** This is the recurring path, and it needs no artifact
   and no old engine version.
3. **`hub/data/feast_name_map.json`.** The fallback for rows written before `sample_date`
   existed — see below. Keyed on a normalized form of the old name.

Anything left unresolved is reported and **left alone**, never deleted: whatever it is, its
contexts and icon are not reproducible.

Where a rename lands on a name another row already holds, the two are merged by
`hub.services.feast_merge.survivor` — the same rule the re-key migration applied. The oldest row
survives (it is the one holding the accumulated enrichment), every context is reparented, thumbs
are summed, and icon and designation are taken from the first row that has them.

## The one-time map, and why it exists

Production's names were written by three sources that do not agree with each other:

| Era | Source | What its names look like |
|---|---|---|
| through 2026-07-21 | the sacredtradition.am scrape | the site's raw text: `<br>` components jammed together, HTML entities un-unescaped, the source's Cyrillic homoglyphs intact |
| 2026-07-21 → 1.3.0 | engine 1.1.x / 1.2.x | the engine's own names, before the observance catalog and the per-date position labels |
| from 1.3.0 | engine 1.3.0 | `Saint(s)` folded to `St(s).`, 122 component spellings corrected, position and eve labels regenerated per date |

None of those rows has a `sample_date`, so none can be recomputed. `scripts/build_feast_name_map.py`
recovers them by joining every old source to the current engine **by date** — the lectionary
repo's `dev/reference_data/*.json` holds the same `dname` field the scraper read, and every old
release is on PyPI. It is a one-time artifact; the procedure above never needs it again.

Regenerating it (only if a release renames a target the map points at):

```bash
python scripts/build_feast_name_map.py \
    --reference-data ../armenian_lectionary/dev/reference_data
```

The script fails rather than writing an unsafe map if two distinct old names fold to one key while
disagreeing about where they should land.

## Reading the audit

`audit_feast_duplicates` is read-only and reports three things:

- **unreachable** — the damage, already done. Names the engine never emits.
- **pending remap** — the same damage caught earlier: a row whose own `sample_date` the engine now
  calls something else. Still reachable today, but not once every date it serves has moved.
- **rows with no `sample_date`** — rows a future rename cannot be followed from. `remap_feast_names`
  backfills them.

All three should read zero after step 4.
