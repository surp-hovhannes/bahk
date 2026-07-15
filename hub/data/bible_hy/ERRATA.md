# Errata & resolutions — `bible_hy`

Human-readable companion to `errata.json`. Explains each correction the scraper
applies to the raw sacredtradition.am library text, and records the deferred
(not-yet-repaired) defects. See `TRIAGE.md` for how these were discovered.

## How the scraper uses this

`scripts/scrape_bible_hy.py` loads `errata.json` and, per book:

1. applies **`resolutions`** (chapter/verse relabels, collision policy) while parsing;
2. for **`deferred`** books, resolves collisions with a safe `keep_first` fallback;
3. treats **any remaining within-chapter duplicate verse number as a HARD ERROR**
   (`exit 1`), so a new/unknown defect can never silently overwrite a verse.

Books needing human confirmation are marked `needs_review` and logged as `REVIEW*`.

## Resolutions (lectionary-served defects)

Each was cross-checked against the daily-readings-page block for a date the
reading occurs (found via the lectionary engine).

| Book | Fix | Validation vs daily page |
|------|-----|--------------------------|
| **Isaiah 7** | verse relabel: 2nd `3` → `9` (v9 mislabeled) | ✅ `Isaiah 7.1-9` exact match (2026-08-09) |
| **Matthew 18** | verse relabel: 2nd `28` → `29` (v29 mislabeled) | ✅ `Matthew 18.23-35` exact match (2026-07-24) |
| **Proverbs 24** | collision policy `keep_first` (keep canonical 24:1-5) | ✅ `Proverbs 24.1-12` exact match (2026-02-05) |
| **Haggai 2** | chapter relabel `→2` + verse relabel 1st `2`→`1` | ✅ `Haggai 2.10` exact match (2026-07-28) |
| **Song of Songs 7** | chapter relabel `→7` (structure only) | Chapters 6-8 → **served from daily page**, see below |

### Song of Songs 6-8 → daily-sourced (`daily_source` in `errata.json`)

Song 6-8 is **not** served from the corpus. Two problems make the verse-keyed
corpus unreliable there:

1. **Verse-division divergence:** the library page divides Song 6-8 differently
   from the lectionary numbering (the daily page's Song 6:9 `Դուստրերը տեսան
   նրան…` is not a discrete verse in the library text).
2. **Discontinuous engine references:** the lectionary engine emits Song readings
   as *spans* that the daily page serves as multiple separate pericopes — e.g.
   engine `Song 2.8-6.12` is served as daily `2.8-16` + `5.1` + `6.8-11`, and
   engine `6.9-11` shows as daily `6.8-11`. A contiguous corpus range would
   include unread verses.

Resolution: `errata.json` → `daily_source` marks `SNG` chapters 6-8. `load_bible_hy`
**skips** loading them into `BibleVerse` (so the corpus never returns divergent
text), and the serving cutover routes any Reading touching Song 6-8 to the
existing daily-readings scrape (`hub/utils.scrape_armenian_reading_texts`).

**Follow-up for the maintainer:** the discontinuous-span issue may affect Song
readings beyond ch6-8 (any multi-pericope span). The engine's Song reference
representation is worth a separate review; widen the `daily_source` range if
needed. Song readings that fall within a single library chapter (1-5) still
compose from the corpus.

## Deferred — defects in chapters NO lectionary reading references

Left with a `keep_first` fallback (documented, not silently lost) for later
repair. None affect served readings.

| Book | Chapters | Cause |
|------|----------|-------|
| Numbers 7 | 7 | source typo (v80 → `8`) |
| Judges 3 | 3 | source typo (vv16-18 → 26-28) |
| 2 Kings 10 | 10 | source typo |
| 2 Chronicles | 2, 11, 18 | source typos |
| Esther | all | Greek additions → duplicate chapter numbers |
| 2 Maccabees 4 | 4 | source typo |
| Psalms | 9, 16, 60, 113, 150 | LXX psalter merges (Ps 9=Heb 9+10, 113=Heb 114+115) |
| Sirach | all | dirty source: empty headings drop 13 chapter numbers |
| Revelation 3 | 3 | source typo |

To repair one later: add a `resolutions` entry (verse relabels / collision policy),
remove it from `deferred`, and re-run the scraper — it will hard-error if the fix
is incomplete.
