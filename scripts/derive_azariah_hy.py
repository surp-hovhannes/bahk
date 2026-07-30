#!/usr/bin/env python3
"""Derive a standalone Armenian ``S3Y`` (Prayer of Azariah) corpus book from Daniel 3.

One-time build tool, run offline. The Nor Ejmiatsin corpus has no standalone Prayer of
Azariah / Song of the Three Young Men book -- it's embedded inside Armenian Daniel 3:24-90
(Armenian Daniel 3 runs to 97 verses: 1-23 narrative, 24-90 the addition, 91-97 narrative
resumes). This script extracts that block, renumbers it 1-N, and writes a synthetic per-book
JSON file in the same shape ``load_bible_hy`` expects, plus reports the row to add to
``usfm_mapping.json``.

This is the offline corpus-authoring half of the Azariah composite -- see
``hub/services/verse_mapping.py``'s ``"azariah-in-daniel"`` rule (id) for the runtime
gap-awareness half (the Armenian block is 67 verses; English KJVAIC's S3Y is 68 -- a doxology
verse KJV splits that Armenian keeps whole).

Usage:
    python scripts/derive_azariah_hy.py                 # write hub/data/bible_hy/books/2876.json
    python scripts/derive_azariah_hy.py --dry-run       # report only, write nothing
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "hub" / "data" / "bible_hy"

DANIEL_ID_ART = 2847
DANIEL_CHAPTER = 3
ADDITION_START_VERSE = 24
ADDITION_END_VERSE = 90

S3Y_ID_ART = 2876
S3Y_NAME_HY = "Աղօթք Ազարիայ"
S3Y_TITLE_HY = "ԱՂՕԹՔ ԱԶԱՐԻԱՅ"
S3Y_NOTE = (
    "Embedded in Armenian Daniel 3:24-90 (67 verses), extracted and renumbered 1-67. "
    "English KJVAIC's S3Y has 68 verses (a doxology verse split that Armenian keeps whole); "
    "the tail is unreconciled -- see hub/data/verse_mappings.json's 'azariah-in-daniel' rule."
)


def derive(root: Path) -> dict:
    """Return the synthetic S3Y book document derived from Daniel 3:24-90."""
    daniel_path = root / "books" / f"{DANIEL_ID_ART}.json"
    daniel = json.loads(daniel_path.read_text(encoding="utf-8"))
    chapter3 = daniel["chapters"][str(DANIEL_CHAPTER)]

    verses = {}
    for verse_num in range(ADDITION_START_VERSE, ADDITION_END_VERSE + 1):
        text = chapter3[str(verse_num)]
        renumbered = verse_num - ADDITION_START_VERSE + 1
        verses[str(renumbered)] = text

    return {
        "idArt": S3Y_ID_ART,
        "name_hy": S3Y_NAME_HY,
        "title_hy": S3Y_TITLE_HY,
        "version": daniel["version"],
        "source": f"derived from idArt {DANIEL_ID_ART} (Daniel) {DANIEL_CHAPTER}:{ADDITION_START_VERSE}-{ADDITION_END_VERSE}",
        "usfm": "S3Y",
        "num_chapters": 1,
        "num_verses": len(verses),
        "needs_review": True,
        "chapters": {"1": verses},
        "superscriptions": {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(DEFAULT_ROOT), help="Corpus root directory.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    args = parser.parse_args()

    root = Path(args.dir)
    doc = derive(root)

    print(f"Derived S3Y: {doc['num_verses']} verses (idArt {S3Y_ID_ART}).")
    print(f"Head check -- S3Y 1:1: {doc['chapters']['1']['1'][:60]}...")
    print(f"Tail check -- S3Y 1:{doc['num_verses']}: {doc['chapters']['1'][str(doc['num_verses'])][:60]}...")

    if args.dry_run:
        print("Dry run -- no file written.")
        return

    out_path = root / "books" / f"{S3Y_ID_ART}.json"
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    print(
        f"Add to usfm_mapping.json: "
        f'{{"idArt": {S3Y_ID_ART}, "usfm": "S3Y", "english": "Prayer of Azariah", '
        f'"name_hy": "{S3Y_NAME_HY}", "note": "..."}}'
    )


if __name__ == "__main__":
    main()
