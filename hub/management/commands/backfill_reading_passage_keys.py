"""Recompute Reading.passage_key. The escape hatch when the key derivation changes."""
import logging

from django.core.management.base import BaseCommand

from hub.constants import passage_key
from hub.models import Reading

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Recompute Reading.passage_key. Run with --all after changing BOOK_NAME_TO_USFM, "
        "normalize_book_name, or the key format: rows keep whatever key they were saved "
        "with, so old and new rows would otherwise land in separate dedup groups and "
        "each retrieval would be made twice. Without --all, only rows with no key are "
        "filled, which is what a BOOK_NAME_TO_USFM addition needs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            dest="recompute_all",
            help="Recompute every row, not just those with an empty key",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing",
        )

    def handle(self, *args, **options):
        recompute_all = options["recompute_all"]
        dry_run = options["dry_run"]

        qs = Reading.objects.all() if recompute_all else Reading.objects.filter(passage_key="")

        # Grouped by citation: there are ~1,124 distinct passages no matter how many
        # reading rows exist, so this is bounded by the corpus rather than the table.
        citations = qs.values_list(
            "book", "start_chapter", "start_verse", "end_chapter", "end_verse",
        ).distinct()

        changed = unmappable = 0
        for book, start_ch, start_v, end_ch, end_v in list(citations):
            key = passage_key(book, start_ch, start_v, end_ch, end_v)
            rows = Reading.objects.filter(
                book=book, start_chapter=start_ch, start_verse=start_v,
                end_chapter=end_ch, end_verse=end_v,
            )
            if not recompute_all:
                rows = rows.filter(passage_key="")
            if not key:
                unmappable += rows.count()
                self.stdout.write(
                    self.style.WARNING(f"  no USFM mapping for {book!r} -- leaving key empty")
                )
                continue
            stale = rows.exclude(passage_key=key)
            count = stale.count()
            if not count:
                continue
            changed += count
            if not dry_run:
                stale.update(passage_key=key)

        verb = "would change" if dry_run else "changed"
        self.stdout.write(self.style.SUCCESS(f"{verb} passage_key on {changed} reading(s)."))
        if unmappable:
            self.stdout.write(
                self.style.WARNING(
                    f"{unmappable} reading(s) have no USFM mapping and can never be "
                    "retrieved. Add their books to BOOK_NAME_TO_USFM in hub/constants.py."
                )
            )
