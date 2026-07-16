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
| **Song of Songs 7** | chapter relabel `→7` + `chapter_override` (verses 1-13) | ✅ serves from corpus; 13 contiguous verses |

### Song of Songs 7 — dropped chapter marker (`chapter_override`)

The source omitted the `7` chapter-number marker, so the parser merged Song 7's
verse 1 into the chapter heading and left a stray bare `7` mid-text; all 13
verses are present and correct, only the markup was broken. Resolution:
`chapter_relabel` moves the block to chapter 7, then `chapter_override` supplies
verses 1-13 verbatim from the **already-scraped** raw HTML (no new network call):
verse 1 = the heading text plus the unlabelled continuation through
`…ուլունքների:`; the stray `7` is dropped. Song 6-8 serve normally from the
corpus (an earlier `daily_source` routing was removed).

> Note: the lectionary engine emits some Song readings as *spans* that the daily
> page splits into separate pericopes (e.g. engine `Song 2.8-6.12` ≈ daily
> `2.8-16` + `5.1` + `6.8-11`). That is an engine reference-representation matter,
> not a corpus-text problem, and is out of scope here.

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
