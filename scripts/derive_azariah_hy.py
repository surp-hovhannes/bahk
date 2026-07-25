"""Derive the standalone Armenian Prayer of Azariah / Song of the Three (USFM ``S3Y``).

The Nor Ejmiatsin corpus has no standalone ``S3Y`` book -- the addition is embedded inside
**Daniel 3** (Armenian Daniel 3 runs to 97 verses).  The liturgical reading cites it as its own
unit ("Azariah 1-68", the trailing half of the composite "Daniel 3.1-23, Azariah. 1-68"), so we
extract Armenian **Daniel 3:24-90** (the Prayer of Azariah + Song of the Three) into a standalone
book renumbered from 1, letting English (KJVAIC ``S3Y``) and Armenian compose the same book id.

Boundaries (verified against the corpus text):
  * Daniel 3:23 -- the three fall bound into the furnace (end of the Hebrew narrative).
  * Daniel 3:24 -- "they walked in the fire, praising"  == KJV S3Y verse 1.
  * Daniel 3:25 -- "Azariah stood and prayed"           == KJV S3Y verse 2.
  * Daniel 3:90 -- "all ye servants of the Lord, bless" == end of the Song.
  * Daniel 3:91 -- Nebuchadnezzar's reaction (Hebrew narrative resumes).

So HY verse N = Daniel 3 verse (N + 23), giving S3Y 1-67.  NOTE: English ``S3Y`` is **68** verses;
the doxology splits one verse in KJV that Armenian keeps whole, so the tail is off by one.  The
exact split verse must be pinned against the English text (``needs_review`` stays true until then).

Idempotent: rewrites ``books/<S3Y_IDART>.json`` from the current Daniel file each run.

Usage::

    python scripts/derive_azariah_hy.py                 # default corpus dir
    python scripts/derive_azariah_hy.py --dir hub/data/bible_hy
"""
import argparse
import json
from pathlib import Path

DANIEL_IDART = "2847"
S3Y_IDART = 2876          # synthetic id, appended after the scraped 2801-2875 range
DAN_CHAPTER = "3"
ADDITION_START = 24       # Daniel 3:24 -> S3Y 1
ADDITION_END = 90         # Daniel 3:90 -> S3Y 67
OFFSET = ADDITION_START - 1  # HY S3Y verse = Daniel 3 verse - OFFSET


def derive(corpus_dir: Path) -> Path:
    books = corpus_dir / "books"
    daniel = json.loads((books / f"{DANIEL_IDART}.json").read_text(encoding="utf-8"))
    dan3 = daniel["chapters"][DAN_CHAPTER]

    verses = {
        str(v - OFFSET): dan3[str(v)]
        for v in range(ADDITION_START, ADDITION_END + 1)
        if str(v) in dan3
    }
    expected = ADDITION_END - ADDITION_START + 1
    if len(verses) != expected:
        raise SystemExit(
            f"Expected {expected} verses in Daniel {DAN_CHAPTER}:"
            f"{ADDITION_START}-{ADDITION_END}, found {len(verses)}."
        )

    doc = {
        "idArt": S3Y_IDART,
        "name_hy": "Աղօթք Ազարիայ",
        "title_hy": "ԱՂՕԹՔ ԱԶԱՐԻԱՅ",
        "version": daniel.get("version", "Նոր Էջմիածին"),
        "source": f"derived: {daniel['idArt']} Daniel {DAN_CHAPTER}:{ADDITION_START}-{ADDITION_END}",
        "usfm": None,
        "num_chapters": 1,
        "num_verses": len(verses),
        # Tail numbering (67 HY vs 68 EN) is unreconciled until the English S3Y text is available.
        "needs_review": True,
        "chapters": {"1": verses},
    }

    out = books / f"{S3Y_IDART}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="hub/data/bible_hy", help="Corpus directory.")
    args = ap.parse_args()
    path = derive(Path(args.dir))
    print(f"Wrote {path}")
