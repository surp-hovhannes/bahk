# Armenian Bible corpus (`bible_hy`)

Offline, verse-keyed corpus of the Eastern Armenian Bible (**Նոր Էջմիածին /
Nor Ejmiatsin**, 1994) scraped once from sacredtradition.am. It replaces the
per-request scraping in `hub/services/reading_text_service.py:fetch_armenian_text`.

## Files

| Path | Purpose |
|------|---------|
| `usfm_mapping.json` | 75-book manifest: `idArt` → `usfm` (+ Armenian name, notes). Source of truth for book identity; also usable as the scraper's `--manifest`. |
| `books/<idArt>.json` | One file per book, verses keyed by **actual** verse number: `{"chapters": {"1": {"1": "…"}}, "superscriptions": {"1": "…"}}`. Git-tracked source of truth. |
| `validation_report.json` | Written by the scraper; per-book anomaly report (dirty source, versification gaps). Review before loading to prod. |

Verses are keyed by number, not position, because this edition has real
non-contiguous versification (e.g. **Tobit 1 skips verses 11/14/16/17**). A
positional list would silently misalign every verse after a gap. Chapter
superscriptions (Psalm titles, etc.) are stored as **verse 0**.

## Rebuild pipeline

```bash
# 1. Scrape all 75 books (one-time; ~75 polite requests, ~2 min).
python scripts/scrape_bible_hy.py \
    --manifest hub/data/bible_hy/usfm_mapping.json \
    --out hub/data/bible_hy --raw-dir /tmp/bible_hy_raw --delay 1.0

# 2. Review anomalies flagged for the deuterocanon / dirty source.
cat hub/data/bible_hy/validation_report.json

# 3. Load into the BibleVerse table (full rebuild for this version).
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 \
    python manage.py load_bible_hy            # add --dry-run to preview
```

## Known source quirks to verify

These are flagged by the scraper's `validation_report.json`; confirm they are
faithful before trusting the load:

- **Sirach (`SIR`, idArt 2829):** dirty source HTML — empty `<center><b></b></center>`
  chapter headings (dropped chapter numbers). Needs manual reconciliation.
- **Psalms (`PSA`):** 150 chapters (no Ps 151); 76 superscriptions stored as verse 0.
- **Tobit (`TOB`):** legitimately non-contiguous verse numbers (not a parse bug).

## TODO — verify book identity against English (once loaded)

The USFM mapping was assigned from the Armenian book names; the tradition-specific
cases below should be spot-checked in the admin by comparing a retrieved Armenian
reading against the English (`text` vs `text_hy`) for the **same** `Reading`:

- [ ] `1ES` **1 Esdras** (Ա Եզրաս) vs `EZR` **Ezra** (Բ Եզրաս) — easy to swap.
- [ ] `EST` **Esther** — Armenian uses the expanded/Greek Esther (API.Bible `ESG`).
- [ ] `BAR` **Baruch** (Բարուքի թուղթը) — may append the Letter of Jeremiah (`LJE`).
- [ ] `DAN` **Daniel** — embeds Susanna / Bel / Song of the Three inline.
- [ ] `1SA`–`2KI` — labelled "1–4 Kingdoms" (Ա–Դ Թագաւորութիւններ).
- [ ] Confirm chapter/verse **counts** line up book-by-book with the English source.
