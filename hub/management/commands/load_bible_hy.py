"""Load the Armenian Bible corpus into the ``BibleVerse`` table.

Reads verse-keyed, per-book JSON (the git-tracked source of truth produced by
``scripts/scrape_bible_hy.py``) plus a ``usfm_mapping.json`` (idArt -> USFM) and
performs a full rebuild of the rows for the given translation ``version``.

Layout expected under ``--dir`` (default ``hub/data/bible_hy``)::

    usfm_mapping.json          # [{"idArt": 2801, "usfm": "GEN", ...}, ...]
    books/2801.json            # {"chapters": {"1": {"1": "..."}}, "superscriptions": {...}}
    books/2802.json
    ...

Usage::

    python manage.py load_bible_hy                 # full rebuild for Nor Ejmiatsin
    python manage.py load_bible_hy --dry-run       # parse + report, write nothing
    python manage.py load_bible_hy --dir /path/to/bible_hy --version "Նոր Էջմիածին"
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hub.models import BibleVerse

DEFAULT_DIR = Path(settings.BASE_DIR) / "hub" / "data" / "bible_hy"
BATCH_SIZE = 2000
EXPECTED_BOOKS = 76  # 75 scraped Nor Ejmiatsin books + derived S3Y (Prayer of Azariah)


class Command(BaseCommand):
    help = "Load the verse-keyed Armenian Bible corpus JSON into the BibleVerse table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir", default=str(DEFAULT_DIR),
            help=f"Corpus directory containing usfm_mapping.json and books/ (default: {DEFAULT_DIR}).",
        )
        parser.add_argument(
            "--bible-version", dest="bible_version", default=BibleVerse.NOR_EJMIATSIN,
            help="Translation identifier stored on each row.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Parse and report counts without deleting or writing any rows.",
        )

    def handle(self, *args, **opts):
        root = Path(opts["dir"])
        version = opts["bible_version"]
        dry_run = opts["dry_run"]

        mapping_path = root / "usfm_mapping.json"
        books_dir = root / "books"
        if not mapping_path.exists():
            raise CommandError(f"Missing mapping file: {mapping_path}")
        if not books_dir.is_dir():
            raise CommandError(f"Missing books directory: {books_dir}")

        # idArt (str) -> USFM
        mapping = {
            str(row["idArt"]): row["usfm"]
            for row in json.loads(mapping_path.read_text(encoding="utf-8"))
        }

        # USFM -> {chapters to omit from the corpus}. Reserved for future
        # daily-sourced overrides; no entries are defined yet, so nothing is
        # skipped today.
        daily_source: dict[str, set[int]] = {}
        errata_path = root / "errata.json"
        if errata_path.exists():
            spec = json.loads(errata_path.read_text(encoding="utf-8")).get("daily_source", {})
            daily_source = {
                usfm: set(v["chapters"])
                for usfm, v in spec.items()
                if not usfm.startswith("_")
            }

        rows: list[BibleVerse] = []
        loaded_books = 0
        skipped_daily = 0
        per_book: list[tuple[str, int]] = []

        for book_path in sorted(books_dir.glob("*.json")):
            id_art = book_path.stem
            usfm = mapping.get(id_art)
            if usfm is None:
                self.stderr.write(f"  WARN {book_path.name}: idArt {id_art} not in mapping; skipped.")
                continue

            doc = json.loads(book_path.read_text(encoding="utf-8"))
            before = len(rows)
            daily_chapters = daily_source.get(usfm, set())

            for chapter_str, verses in doc.get("chapters", {}).items():
                chapter = int(chapter_str)
                if chapter in daily_chapters:
                    skipped_daily += 1
                    continue
                for verse_str, text in verses.items():
                    rows.append(BibleVerse(
                        version=version, book=usfm,
                        chapter=chapter, verse=int(verse_str), text=text,
                    ))
            # Superscriptions -> verse 0
            for chapter_str, sup in doc.get("superscriptions", {}).items():
                if int(chapter_str) in daily_chapters:
                    continue
                rows.append(BibleVerse(
                    version=version, book=usfm,
                    chapter=int(chapter_str), verse=0, text=sup,
                ))

            loaded_books += 1
            per_book.append((usfm, len(rows) - before))

        if skipped_daily:
            served = ", ".join(f"{u} {sorted(c)}" for u, c in daily_source.items())
            self.stdout.write(f"Skipped {skipped_daily} daily-sourced chapter(s): {served}.")

        if not rows:
            raise CommandError(f"No verses parsed from {books_dir} — is the corpus populated?")

        self.stdout.write(
            f"Parsed {len(rows)} rows across {loaded_books} book(s) for version {version!r}."
        )
        if loaded_books != EXPECTED_BOOKS:
            self.stdout.write(self.style.WARNING(
                f"  Expected {EXPECTED_BOOKS} books, found {loaded_books}. "
                "Run the scraper for the full corpus before loading to production."
            ))

        if dry_run:
            for usfm, n in per_book:
                self.stdout.write(f"  {usfm:>3}: {n} rows")
            self.stdout.write(self.style.SUCCESS("Dry run — no rows written."))
            return

        with transaction.atomic():
            deleted, _ = BibleVerse.objects.filter(version=version).delete()
            BibleVerse.objects.bulk_create(rows, batch_size=BATCH_SIZE)

        self.stdout.write(self.style.SUCCESS(
            f"Rebuilt {version!r}: deleted {deleted} old row(s), inserted {len(rows)}."
        ))
