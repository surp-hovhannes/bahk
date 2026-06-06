import csv
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from hub.models import FeastContext, ReadingContext


class Command(BaseCommand):
    help = "Audit thumbs feedback totals for generated reading and feast contexts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            default="365d",
            help="Lookback window in days, formatted like 30d. Defaults to 365d.",
        )
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Emit CSV instead of a plain text table.",
        )

    def handle(self, *args, **options):
        days = self._parse_since(options["since"])
        since = timezone.now() - timedelta(days=days)
        totals = defaultdict(lambda: {"contexts": 0, "thumbs_up": 0, "thumbs_down": 0})

        for row in self._context_rows(
            ReadingContext,
            default_applies_to="readings",
            since=since,
        ):
            self._add_row(totals, row)

        for row in self._context_rows(
            FeastContext,
            default_applies_to="feasts",
            since=since,
        ):
            self._add_row(totals, row)

        rows = [
            {
                "applies_to": applies_to,
                "contexts": values["contexts"],
                "thumbs_up": values["thumbs_up"],
                "thumbs_down": values["thumbs_down"],
            }
            for applies_to, values in sorted(totals.items())
        ]

        if options["csv"]:
            self._write_csv(rows)
        else:
            self._write_table(rows)

    def _parse_since(self, value):
        if not value.endswith("d"):
            raise CommandError("--since must be a day duration like 30d")

        raw_days = value[:-1]
        if not raw_days.isdigit():
            raise CommandError("--since must be a day duration like 30d")

        days = int(raw_days)
        if days < 0:
            raise CommandError("--since must be a non-negative day duration")
        return days

    def _context_rows(self, model, *, default_applies_to, since):
        return (
            model.objects.filter(time_of_generation__gte=since)
            .annotate(
                effective_applies_to=Coalesce(
                    "prompt__applies_to",
                    Value(default_applies_to),
                )
            )
            .values("effective_applies_to")
            .annotate(
                contexts=Count("id"),
                thumbs_up=Coalesce(Sum("thumbs_up"), Value(0)),
                thumbs_down=Coalesce(Sum("thumbs_down"), Value(0)),
            )
            .order_by("effective_applies_to")
        )

    def _add_row(self, totals, row):
        applies_to = row["effective_applies_to"]
        totals[applies_to]["contexts"] += row["contexts"] or 0
        totals[applies_to]["thumbs_up"] += row["thumbs_up"] or 0
        totals[applies_to]["thumbs_down"] += row["thumbs_down"] or 0

    def _write_csv(self, rows):
        writer = csv.writer(self.stdout)
        writer.writerow(["applies_to", "contexts", "thumbs_up", "thumbs_down"])
        for row in rows:
            writer.writerow(
                [
                    row["applies_to"],
                    row["contexts"],
                    row["thumbs_up"],
                    row["thumbs_down"],
                ]
            )

    def _write_table(self, rows):
        self.stdout.write("applies_to contexts thumbs_up thumbs_down")
        for row in rows:
            self.stdout.write(
                f"{row['applies_to']} {row['contexts']} "
                f"{row['thumbs_up']} {row['thumbs_down']}"
            )
