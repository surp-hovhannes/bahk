#!/usr/bin/env python3
"""Scrape the Eastern Armenian (1994 / Nor Ejmiatsin) Bible from sacredtradition.am.

One-time build tool. Fetches all 75 books via the site's AJAX endpoint
(``getBible.php?idArt=<id>``), parses each into a verse-addressable structure,
validates it, and writes one JSON file per book plus a manifest and a
validation report.

Source structure (per book payload):
    <center><b>BOOK TITLE</b></center>
    <center><b>N[ superscription]</b></center>   <- chapter heading (N = number)
    1 verse text: 2 verse text: ...              <- verses run inline, prefixed
    <br><br>                                        by Arabic-digit verse numbers
    <center><b>N+1 ...</b></center>
    ...

Key facts that drive the parser (empirically validated):
  * Arabic digits appear ONLY as verse/chapter numbers — all quantities in the
    text itself are spelled out in Armenian words. So a leading integer is
    unambiguously a verse marker.
  * Verse numbers are NOT always contiguous (e.g. Tobit 1 skips v11, v14) —
    real deuterocanonical versification. => store by explicit verse number.
  * Psalm (and some other) chapter headings carry a superscription after the
    number: ``<center><b>1 Սաղմոս Դաւթի:</b></center>``. Preserved separately.
  * A few books (e.g. Sirach) have malformed source: empty ``<center><b></b>``
    headings / dropped chapter numbers. These are flagged, not silently parsed.

Usage:
    python scrape_bible_hy.py --out ./bible_hy            # full run (75 books)
    python scrape_bible_hy.py --out ./bible_hy --only 2820,2829   # a subset
    python scrape_bible_hy.py --out ./bible_hy --from-dir ./raw   # parse cached HTML
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("scrape_bible_hy")

BASE = "https://www.sacredtradition.am/Library/getBible.php?idArt={id}&L="
FIRST_ID, LAST_ID = 2801, 2875  # Genesis .. Revelation (75 books, Armenian canon)
USER_AGENT = "bahk-bible-corpus/1.0 (one-time liturgical corpus fetch; contact: <you>)"

# Chapter heading: a centered-bold block whose content STARTS with an integer.
# Group 1 = chapter number, group 2 = optional superscription text.
CHAP_RE = re.compile(r"<center><b>(\d+)((?:\s[^<]*)?)</b></center>")
# Verse marker: an integer that begins a verse (start-of-text or after whitespace),
# followed by whitespace + verse text.
VERSE_RE = re.compile(r"(?:(?<=\s)|^)(\d+)\s")
BODY_ANCHOR = "<center><b>"


# --------------------------------------------------------------------------- #
#  Fetch
# --------------------------------------------------------------------------- #
def fetch(idart: int, *, retries: int = 3, delay: float = 1.0) -> str:
    url = BASE.format(id=idart)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, "bad status", resp.headers, None)
                return resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last = exc
            log.warning("fetch %s attempt %d/%d failed: %s", idart, attempt, retries, exc)
            time.sleep(delay * attempt)
    raise RuntimeError(f"failed to fetch idArt={idart}: {last}")


# --------------------------------------------------------------------------- #
#  Parse
# --------------------------------------------------------------------------- #
def parse_book(html: str, *, resolution: dict | None = None, deferred: dict | None = None) -> dict:
    """Parse one book payload into {title_hy, chapters, superscriptions, anomalies, errors}.

    ``resolution``/``deferred`` are this book's entries from ``errata.json``.
    Errata are applied while parsing (chapter/verse relabels, collision policy);
    any within-chapter duplicate verse number left unresolved is reported in
    ``errors`` so the driver can fail loudly instead of silently overwriting.
    """
    start = html.find(BODY_ANCHOR)
    if start == -1:
        raise ValueError("no body anchor found")
    body = html[start:]

    title_m = re.match(r"<center><b>(.*?)</b></center>", body)
    title = title_m.group(1).strip() if title_m else ""

    anomalies: list[str] = []

    # Detect empty/dropped chapter headings (dirty source, e.g. Sirach).
    empty_headings = len(re.findall(r"<center><b>\s*</b></center>", body))
    if empty_headings:
        anomalies.append(f"{empty_headings} empty chapter heading(s) — dropped chapter numbers")

    marks = list(CHAP_RE.finditer(body))
    if not marks:
        anomalies.append("no chapter headings matched")
        return {"title_hy": title, "chapters": {}, "superscriptions": {},
                "anomalies": anomalies, "errors": [], "needs_review": False}

    # ---- errata inputs for this book -------------------------------------- #
    entries = (resolution or {}).get("entries", [])
    relabels = [(e["match_superscription_prefix"], e["to_chapter"])
                for e in entries if e["type"] == "chapter_relabel"]
    verse_relabels = [e for e in entries if e["type"] == "verse_relabel"]
    policies = {e["chapter"]: e["policy"] for e in entries if e["type"] == "collision_policy"}
    overrides = {e["chapter"]: e["verses"] for e in entries if e["type"] == "chapter_override"}
    deferred_spec = (deferred or {}).get("chapters", [])
    defer_all = deferred_spec == "all"          # whole book is known-dirty & unread
    deferred_chapters = set() if defer_all else set(deferred_spec)
    deferred_fallback = (deferred or {}).get("fallback", "keep_first")

    # ---- collect ordered verse occurrences per (effective) chapter -------- #
    # Each occurrence is a mutable [vnum, text] so verse_relabel can renumber it.
    chap_occ: dict[int, list] = {}
    superscriptions: dict[int, str] = {}
    for i, m in enumerate(marks):
        cnum = int(m.group(1))
        sup = (m.group(2) or "").strip().rstrip(":").strip()
        for prefix, to_chapter in relabels:          # chapter_relabel (before verses)
            if sup.startswith(prefix):
                cnum = to_chapter
                break
        seg_start = m.end()
        seg_end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        seg = body[seg_start:seg_end].replace("<br>", " ")

        occ = chap_occ.setdefault(cnum, [])
        vmarks = list(VERSE_RE.finditer(seg))
        for j, vm in enumerate(vmarks):
            vstart = vm.end()
            vend = vmarks[j + 1].start() if j + 1 < len(vmarks) else len(seg)
            text = re.sub(r"<[^>]+>", "", seg[vstart:vend])   # strip any stray tags
            text = re.sub(r"\s+", " ", text).strip()
            occ.append([int(vm.group(1)), text])
        if sup:
            superscriptions.setdefault(cnum, sup)

    # ---- verse_relabel: renumber the Nth occurrence of a wrong number ------ #
    for e in verse_relabels:
        seen = 0
        for pair in chap_occ.get(e["chapter"], []):
            if pair[0] == e["wrong"]:
                seen += 1
                if seen == e["occurrence"]:
                    pair[0] = e["to"]
                    break

    # ---- resolve collisions; unresolved duplicates are HARD ERRORS -------- #
    chapters: dict[int, dict[int, str]] = {}
    errors: list[str] = []
    for cnum, occ in chap_occ.items():
        if cnum in overrides:
            continue                # chapter text supplied explicitly below
        nums = [p[0] for p in occ]
        dups = sorted({n for n in nums if nums.count(n) > 1})
        policy = policies.get(cnum)
        if dups and policy is None:
            policy = deferred_fallback if (defer_all or cnum in deferred_chapters) else None
        if dups and policy is None:
            errors.append(f"ch {cnum}: unresolved duplicate verse number(s) {dups}")
            policy = "keep_first"   # build something anyway for inspection
        verses: dict[int, str] = {}
        for num, text in occ:
            if num in verses and policy != "keep_last":
                continue            # keep_first (or default): first occurrence wins
            verses[num] = text
        chapters[cnum] = verses

    # ---- chapter_override: hand-corrected verse text replaces a parsed chapter #
    # (used where source markup is too mangled to parse, e.g. a dropped chapter
    #  number that merges verse text; verses are given verbatim in errata.json).
    for cnum, ov_verses in overrides.items():
        chapters[cnum] = {int(v): t for v, t in ov_verses.items()}
        superscriptions.pop(cnum, None)   # verse 1 now holds the heading text

    # ---- consistency warnings (some gaps are legitimate deuterocanonical) -- #
    nums = sorted(chapters)
    if nums and nums != list(range(1, nums[-1] + 1)):
        anomalies.append(f"non-contiguous chapters; missing {sorted(set(range(1, nums[-1] + 1)) - set(nums))}")
    for c, v in chapters.items():
        if v:
            vnums = sorted(v)
            if vnums != list(range(1, vnums[-1] + 1)):
                anomalies.append(
                    f"ch {c}: non-contiguous verses; missing {sorted(set(range(1, vnums[-1] + 1)) - set(vnums))}")

    return {
        "title_hy": title,
        "chapters": chapters,
        "superscriptions": superscriptions,
        "anomalies": anomalies,
        "errors": errors,
        "needs_review": (resolution or {}).get("needs_review", False),
    }


# --------------------------------------------------------------------------- #
#  Serialize — one JSON per book, verses keyed by string verse number
# --------------------------------------------------------------------------- #
def book_document(idart: int, name_hy: str, parsed: dict) -> dict:
    # JSON object keys are strings; keep numeric order explicit and sorted.
    chapters = {
        str(c): {str(v): parsed["chapters"][c][v] for v in sorted(parsed["chapters"][c])}
        for c in sorted(parsed["chapters"])
    }
    superscriptions = {str(c): parsed["superscriptions"][c] for c in sorted(parsed["superscriptions"])}
    return {
        "idArt": idart,
        "name_hy": name_hy,
        "title_hy": parsed["title_hy"],
        "version": "Նոր Էջմիածին",          # Eastern Armenian (1994)
        "source": "sacredtradition.am",
        # usfm / canonical id intentionally left null: reconcile against
        # hub/constants.py BOOK_NAME_TO_USFM before loading into bahk.
        "usfm": None,
        "num_chapters": len(chapters),
        "num_verses": sum(len(v) for v in chapters.values()),
        "needs_review": parsed.get("needs_review", False),
        "chapters": chapters,
        "superscriptions": superscriptions,
    }


# --------------------------------------------------------------------------- #
#  Driver
# --------------------------------------------------------------------------- #
def load_manifest(path: Path) -> list[dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    # Fallback: names filled in after first run; ids are contiguous.
    return [{"idArt": i, "name_hy": ""} for i in range(FIRST_ID, LAST_ID + 1)]


def load_errata(path: Path | None) -> tuple[dict, dict]:
    """Return (resolutions, deferred) keyed by idArt string; empty if absent."""
    if not path or not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    deferred = {k: v for k, v in data.get("deferred", {}).items() if not k.startswith("_")}
    return data.get("resolutions", {}), deferred


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("bible_hy"), help="output directory")
    ap.add_argument("--manifest", type=Path, default=Path("book_manifest.json"))
    ap.add_argument("--only", default="", help="comma-separated idArt subset")
    ap.add_argument("--from-dir", type=Path, default=None, help="parse cached raw/<id>.html instead of fetching")
    ap.add_argument("--raw-dir", type=Path, default=None, help="also save fetched HTML here")
    ap.add_argument("--errata", type=Path, default=None,
                    help="errata.json path (default: <out>/errata.json)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (politeness)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    manifest = load_manifest(args.manifest)
    if args.only:
        want = {int(x) for x in args.only.split(",")}
        manifest = [b for b in manifest if b["idArt"] in want]

    resolutions, deferred = load_errata(args.errata or (args.out / "errata.json"))

    books_dir = args.out / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    if args.raw_dir:
        args.raw_dir.mkdir(parents=True, exist_ok=True)

    report: list[dict] = []
    total_verses = 0
    error_books: list[int] = []
    for b in manifest:
        idart, name_hy = b["idArt"], b.get("name_hy", "")
        try:
            if args.from_dir:
                html = (args.from_dir / f"{idart}.html").read_text(encoding="utf-8")
            else:
                html = fetch(idart, delay=args.delay)
                if args.raw_dir:
                    (args.raw_dir / f"{idart}.html").write_text(html, encoding="utf-8")
                time.sleep(args.delay)

            parsed = parse_book(
                html,
                resolution=resolutions.get(str(idart)),
                deferred=deferred.get(str(idart)),
            )
            doc = book_document(idart, name_hy, parsed)
            out = books_dir / f"{idart}.json"
            out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            total_verses += doc["num_verses"]
            errors = parsed["errors"]
            if errors:
                status = "ERROR"
                error_books.append(idart)
            elif parsed["needs_review"]:
                status = "REVIEW*"      # errata applied, flagged for human confirmation
            elif parsed["anomalies"]:
                status = "REVIEW"
            else:
                status = "OK"
            tail = ""
            if errors:
                tail = f"[UNRESOLVED: {'; '.join(errors)}]"
            elif parsed["anomalies"]:
                tail = f"[{len(parsed['anomalies'])} anomalies]"
            log.info("%-8s %5d %-32s ch=%3d v=%5d %s",
                     status, idart, (doc["title_hy"] or name_hy)[:32],
                     doc["num_chapters"], doc["num_verses"], tail)
            report.append({"idArt": idart, "name_hy": name_hy, "title_hy": doc["title_hy"],
                           "num_chapters": doc["num_chapters"], "num_verses": doc["num_verses"],
                           "needs_review": parsed["needs_review"],
                           "errors": errors, "anomalies": parsed["anomalies"]})
        except Exception as exc:  # noqa: BLE001
            log.error("FAILED idArt=%s: %s", idart, exc)
            error_books.append(idart)
            report.append({"idArt": idart, "name_hy": name_hy, "error": str(exc)})

    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    flagged = [r for r in report if r.get("anomalies") or r.get("error") or r.get("errors")]
    log.info("Done. %d books, %d verses. %d flagged -> validation_report.json",
             len(report), total_verses, len(flagged))
    for r in flagged:
        detail = r.get("error") or "; ".join(r.get("errors") or []) or "; ".join(r.get("anomalies") or [])
        log.info("  %-8s %5d %s: %s",
                 "ERROR" if (r.get("error") or r.get("errors")) else "review",
                 r["idArt"], r.get("name_hy", ""), detail[:160])
    if error_books:
        log.error("%d book(s) had UNRESOLVED collisions or fetch failures: %s. "
                  "Add a resolution/deferred entry to errata.json.",
                  len(error_books), sorted(error_books))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
