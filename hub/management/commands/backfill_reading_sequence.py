"""Backfill Reading.sequence for rows that predate the sequence field.

Reading.sequence (added in migration 0056_alter_reading_options_reading_sequence) is kept
correct going forward by hub.services.lectionary_service.persist_readings(), but that function
only runs for a Day when new readings are being imported: hub/views/readings.py skips it
entirely once day.readings.exists() is True, and the migration itself leaves every existing
row's sequence as NULL. Rows created before this fix -- which is the actual reproduction of
issue #324 -- therefore never get a sequence and keep being served in whatever order they
happen to be stored in.

This command is idempotent and safe to re-run: for each Day it recomputes the lectionary
engine's current reading order, matches existing Reading rows to engine entries by (book,
start_chapter, start_verse, end_chapter, end_verse) -- the same fields as the
unique_reading_per_day constraint -- and updates only the sequence of matched rows. It never
creates or deletes Reading rows; a Day whose stored readings don't line up one-to-one with the
engine's current output (e.g. a book name the engine has since renamed) is reported instead of
guessed at.

Mandatory deployment step: run this once after applying migration 0056 (before or alongside
deploying the code that relies on Reading.sequence), e.g.:

    python manage.py backfill_reading_sequence --strict
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hub.models import Church, Day
from hub.services.lectionary_service import get_daily_readings

logger = logging.getLogger(__name__)


def _reading_key(book, start_chapter, start_verse, end_chapter, end_verse):
    return (book, start_chapter, start_verse, end_chapter, end_verse)


class Command(BaseCommand):
    help = (
        "Backfill Reading.sequence for existing rows by matching them against the lectionary "
        "engine's current reading order. Idempotent; never creates or deletes Reading rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--church",
            help="Name of a single church to scope the backfill to. Defaults to all churches.",
        )
        parser.add_argument("--start-date", help="YYYY-MM-DD. Defaults to the earliest Day.")
        parser.add_argument("--end-date", help="YYYY-MM-DD. Defaults to the latest Day.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and report pending updates without saving changes.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with a nonzero status if any Day has readings that can't be matched "
                 "one-to-one against the engine's current output.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        strict = options["strict"]

        days = Day.objects.select_related("church").order_by("date")
        if options["church"]:
            try:
                church = Church.objects.get(name=options["church"])
            except Church.DoesNotExist:
                raise CommandError(f"Church {options['church']!r} does not exist.")
            days = days.filter(church=church)
        if options["start_date"]:
            days = days.filter(date__gte=options["start_date"])
        if options["end_date"]:
            days = days.filter(date__lte=options["end_date"])

        days_scanned = 0
        days_updated = 0
        rows_updated = 0
        unmatched_db_total = 0
        unmatched_engine_total = 0
        mismatched_days = 0

        for day in days.iterator():
            days_scanned += 1
            engine_readings = get_daily_readings(day.date, day.church)

            # Map each engine entry's match key to a queue of its positions, so duplicate
            # citations on the same day (rare, but not prevented by the engine) are matched
            # one-for-one instead of collapsing onto a single index.
            engine_positions_by_key = {}
            for index, reading in enumerate(engine_readings):
                key = _reading_key(
                    reading.get("book_en", reading.get("book")),
                    reading["start_chapter"], reading["start_verse"],
                    reading["end_chapter"], reading["end_verse"],
                )
                engine_positions_by_key.setdefault(key, []).append(index)

            day_updates = []
            unmatched_db = []
            for reading_obj in day.readings.all():
                key = _reading_key(
                    reading_obj.book_en,
                    reading_obj.start_chapter, reading_obj.start_verse,
                    reading_obj.end_chapter, reading_obj.end_verse,
                )
                positions = engine_positions_by_key.get(key)
                if not positions:
                    unmatched_db.append(reading_obj)
                    continue
                index = positions.pop(0)
                if reading_obj.sequence != index:
                    day_updates.append((reading_obj, index))

            unmatched_engine = sum(len(positions) for positions in engine_positions_by_key.values())

            if unmatched_db or unmatched_engine:
                mismatched_days += 1
                unmatched_db_total += len(unmatched_db)
                unmatched_engine_total += unmatched_engine
                for reading_obj in unmatched_db:
                    self.stderr.write(
                        f"{day}: existing reading has no engine match: "
                        f"{reading_obj} (id={reading_obj.pk})"
                    )
                if unmatched_engine:
                    self.stderr.write(
                        f"{day}: {unmatched_engine} engine reading(s) have no matching DB row"
                    )

            if not day_updates:
                continue

            days_updated += 1
            rows_updated += len(day_updates)
            if dry_run:
                continue

            with transaction.atomic():
                for reading_obj, index in day_updates:
                    reading_obj.sequence = index
                    reading_obj.save(update_fields=["sequence"])

        summary = (
            f"days_scanned={days_scanned} days_updated={days_updated} rows_updated={rows_updated} "
            f"mismatched_days={mismatched_days} unmatched_db_rows={unmatched_db_total} "
            f"unmatched_engine_readings={unmatched_engine_total} dry_run={dry_run}"
        )
        self.stdout.write(summary)

        if strict and mismatched_days:
            raise CommandError(
                f"{mismatched_days} day(s) had readings that could not be matched one-to-one "
                "against the engine's current output; see stderr for details."
            )
