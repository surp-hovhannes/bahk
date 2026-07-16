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
| **Esther 1** | `chapter_override` (fold LXX bridge into 1:1; restore v6) | ✅ 22 contiguous verses; not served but corrected for fidelity |

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
| 2 Maccabees 4 | 4 | source typo |
| Psalms | 9, 16, 60, 113, 150 | LXX psalter merges (Ps 9=Heb 9+10, 113=Heb 114+115) |
| Sirach | all | dirty source: empty headings drop 13 chapter numbers |
| Revelation 3 | 3 | source typo |

### Esther — deferred, but one reading IS served (and is intact)

Esther is `keep_first`-deferred for the whole book, **but unlike the rows above it
is not free of lectionary readings.** The engine serves **`Esther 10.4-9`**
(Greek Addition F, the interpretation of Mordecai's dream) for St. Stephen of
Ulnia (~Aug 21-27) and the feast of Joachim & Anna. Those verses were checked
byte-for-byte against the cached raw HTML and are **correct** — the source
numbers chapter 10 cleanly (Addition F = 10:4-13), so the deferral does not touch
them.

This edition follows the Vulgate/Jerome arrangement: the six Greek additions are
**appended as chapters 11-16** (Addition A / Mordecai's dream = 11:2-12:6; the
colophon = 11:1; B-E = 13-16) rather than interleaved before chapter 1. All are
loaded and addressable (`EST 11`-`16`), but **no reading references them.**

**Chapter 1 is now repaired** (`chapter_override`, see resolutions above): the
source stray-marked the LXX bridge clause *«Այս դէպքերից յետոյ, Արտաշէսի օրօք –»*
(Greek 1:1a, "And it came to pass after these things in the days of Artaxerxes")
as a verse `6` sitting *before* the real 1:1 body, so `keep_first` had mislabeled
it and dropped the true verse 6. The override folds the bridge into 1:1 (matching
the Greek and standard translations) and restores verses 2-22.

The remaining `keep_first` fallback covers only the **unread addition-splice
chapters, whose numbering is inconsistent in the source itself** — not a scrape
bug we can mechanically undo:

- **ch4** omits vv 6 / 9 / 10 / 11 at the points where additions splice in (LXX
  versification; the source simply jumps 5→7 and 8→12).
- **ch13** carries two different verse-8s — the end of Addition B (the edict) and
  the start of Addition C (Mordecai's prayer) — so `keep_first` keeps the edict's
  and drops the prayer's opening.
- **ch15** reuses the chapter number for two passages (the chapter-4 dialogue
  filler *and* Addition D), so `keep_first` drops the Addition-D opening (15:4-6).

A faithful reconstruction of ch4/13/15 needs a reference edition to re-versify;
none of it is lectionary-served, so it is documented rather than guessed.

To repair one later: add a `resolutions` entry (verse relabels / collision policy),
remove it from `deferred`, and re-run the scraper — it will hard-error if the fix
is incomplete.
