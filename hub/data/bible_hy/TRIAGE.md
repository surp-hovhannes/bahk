# Scrape anomaly triage — `bible_hy`

Scrape: **75 books, 35,565 verses.** 29 books flagged by `validation_report.json`.
This triages them by whether they affect **actual lectionary readings**.

## Headline

Of the 29 flagged books, **only 5 issues touch a verse the lectionary actually
serves.** Everything else is either a benign known-omitted verse, or data loss in
a chapter/verse no reading references.

## Root causes (three mechanisms)

1. **Benign omitted verses** (most flags): single verses intentionally absent in
   this critical text (e.g. Mark 9:44/46, 11:26; John 5:4; Acts 8:37; Matt 18:11).
   The verse *number* is skipped on purpose — no data lost. **No action.**
2. **Silent overwrite (data loss):** the store keys by verse number, but a few
   chapters reuse a number, so the first occurrence is overwritten. **64 verses
   lost corpus-wide.** Two sub-causes:
   - *Source typos* — a verse number lost a digit: Numbers 7 (`80`→`8`),
     Matthew 18 (`29`→`28`), Judges 3 (`16,17,18`→`26,27,28`), Isaiah 7 (`9`→`3`).
   - *LXX/Armenian renumbering* — Septuagint psalter merges & chapter reorders
     restart numbering mid-chapter: Psalms 9 (=Heb 9+10), 113 (=Heb 114+115), 150;
     Sirach 30; Proverbs 24.
3. **Structural (dropped/mislabeled chapter headings):** Sirach loses 13 chapters
   to empty `<center><b></b></center>` headings; Haggai ch2 and Song ch7 have a
   heading whose number is malformed (`1 <opening words>`).

## MUST-FIX — affects served readings

| Book/ch | Cause | Lost | Affected reading(s) |
|---------|-------|------|---------------------|
| **Isaiah 7** | typo `9`→`3` | v9 | `Isaiah 7.1-9` (v9 is the last verse) |
| **Matthew 18** | typo `29`→`28` | v29 | `Matthew 18.10-35`, `18.23-35` (Unforgiving Servant) |
| **Proverbs 24** | LXX reorder overwrites real 24:1-5 | vv1-5 | `Proverbs 24.1-12` |
| **Haggai 2** | ch2 heading mis-numbered `1` | whole ch | Haggai 2 readings |
| **Song of Songs 7** | ch7 heading `1 «Ի՞նչ…»` | whole ch | Song 7 readings |

## Data loss in chapters NOT read (fix opportunistically)

Numbers 7, Judges 3, 2 Kings 10, 2 Chronicles 2/11/18, Esther 1, 2 Maccabees 4,
Psalms 9/16/60/113/150, Sirach 4/27/30, Revelation 3 — 59 lost verses, none
referenced by the lectionary. Corpus completeness only.

## Structural, NOT read (low priority)

**Sirach**: 13 chapters dropped via empty headings — but the lectionary reads
**no** Sirach, so this doesn't affect bahk. Fix for corpus completeness later.

## Recommended fixes

1. **Harden the scraper:** treat a within-chapter duplicate verse number as a hard
   error (today `book_document` silently overwrites). Nothing should be lost
   invisibly.
2. **Errata overlay:** a small hand-curated `errata.json` of `(book, chapter)` →
   verse relabelings, applied at scrape time. Start with the 5 must-fix; the fix
   direction differs (typos → relabel the mislabeled verse; LXX Proverbs → keep the
   canonically-numbered first occurrence), so a blanket keep-first/last won't do.
   Mirrors the lectionary/corpus ERRATA-overlay pattern.
3. **Sirach / not-read losses:** defer.

## Verify-in-admin checklist (unchanged, still pending load)

USFM identity spot-checks: `1ES`/`EZR`, `EST`(ESG?), `BAR`(+LJE?), `DAN`
(Susanna/Bel confirmed present as ch13/14), `1SA`–`2KI` (1–4 Kingdoms labels).
