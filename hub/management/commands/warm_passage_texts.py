"""Enumerate every passage the lectionary can emit, so no date is ever cold.

The Armenian lectionary resolves to ~1,124 distinct passages across all years, and a
single year covers ~98% of them.  Retrieving that set once means any date, in any year,
can be served without an API call — and the number does not grow as the reading table
does.  This is the one-time cost that makes on-demand retrieval a rarity rather than the
normal path.
"""
import datetime
import logging

from django.core.management.base import BaseCommand, CommandError

from hub.constants import passage_key
from hub.models import PassageText, Reading
from hub.services.lectionary_service import (
    LECTIONARY_MAX_YEAR,
    LECTIONARY_MIN_YEAR,
    _parse_citation,
)
from hub.services.reading_text_service import (
    TEXT_FETCHERS,
    fetch_passage_text,
    prepare_shared_resources,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Enumerate the distinct passages the lectionary emits over its validated year "
        "range and ensure a PassageText row exists for each. Rows are created with "
        "fetched_at=NULL so the refresh task fills them; pass --fetch to retrieve inline. "
        "Run --dry-run first: it reports how many passages are new without spending quota."
    )

    def add_arguments(self, parser):
        parser.add_argument("--start-date", help="YYYY-MM-DD (default: lectionary min year)")
        parser.add_argument("--end-date", help="YYYY-MM-DD (default: lectionary max year)")
        parser.add_argument(
            "--language", action="append", dest="languages",
            help="Language to warm; repeatable. Default: every registered language.",
        )
        parser.add_argument(
            "--fetch", action="store_true",
            help="Retrieve text inline instead of leaving it to the refresh task",
        )
        parser.add_argument(
            "--limit", type=int, help="Stop after this many passages (for a trial run)",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without writing or retrieving",
        )

    def _date(self, value, default):
        if not value:
            return default
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc

    def handle(self, *args, **options):
        import armenian_lectionary

        start = self._date(options["start_date"], datetime.date(LECTIONARY_MIN_YEAR, 1, 1))
        end = self._date(options["end_date"], datetime.date(LECTIONARY_MAX_YEAR, 12, 31))
        if start > end:
            raise CommandError("--start-date must not be after --end-date.")

        languages = options["languages"] or list(TEXT_FETCHERS)
        unknown = [lang for lang in languages if lang not in TEXT_FETCHERS]
        if unknown:
            raise CommandError(
                f"No fetcher registered for {', '.join(unknown)}. "
                f"Known languages: {', '.join(TEXT_FETCHERS)}."
            )

        self.stdout.write(f"Enumerating passages from {start} to {end}...")

        citations: dict[str, tuple] = {}
        unparsed: set[str] = set()
        unmappable: set[str] = set()
        instances = 0
        day = start
        while day <= end:
            result = armenian_lectionary.compute_armenian_lectionary(day)
            for citation_str in result.get("ReadingsList") or []:
                parsed = _parse_citation(citation_str)
                if parsed is None:
                    unparsed.add(citation_str)
                    continue
                instances += 1
                citation = (
                    parsed.get("book_en") or parsed.get("book"),
                    parsed["start_chapter"], parsed["start_verse"],
                    parsed["end_chapter"], parsed["end_verse"],
                )
                key = passage_key(*citation)
                if not key:
                    unmappable.add(str(citation[0]))
                    continue
                citations.setdefault(key, citation)
            day += datetime.timedelta(days=1)

        if not citations:
            raise CommandError("No passages enumerated; check the date range.")

        self.stdout.write(
            f"{instances} reading instances resolve to {len(citations)} distinct passages "
            f"({instances / len(citations):.1f}x dedup)."
        )
        if unparsed:
            self.stdout.write(
                self.style.WARNING(f"{len(unparsed)} citation(s) did not parse: "
                                   + ", ".join(sorted(unparsed)[:5]))
            )
        if unmappable:
            self.stdout.write(
                self.style.WARNING(
                    f"No USFM mapping for: {', '.join(sorted(unmappable))}. "
                    "Add them to BOOK_NAME_TO_USFM in hub/constants.py."
                )
            )

        # Reconciliation: the engine is the same source the readings view uses, so these
        # sets should agree closely. A large "not yet read" count is expected on a fresh
        # database; a large "read but not enumerated" count means the two have diverged.
        in_db = set(
            Reading.objects.exclude(passage_key="")
            .values_list("passage_key", flat=True).distinct()
        )
        self.stdout.write(
            f"  {len(in_db & citations.keys())} already cited by existing readings; "
            f"{len(citations.keys() - in_db)} not yet read; "
            f"{len(in_db - citations.keys())} read but not enumerated."
        )

        keys = sorted(citations)
        if options["limit"]:
            keys = keys[:options["limit"]]

        for language in languages:
            have = set(
                PassageText.objects.filter(language=language, passage_key__in=keys)
                .exclude(text="").values_list("passage_key", flat=True)
            )
            todo = [k for k in keys if k not in have]
            self.stdout.write(
                f"{language}: {len(have)} of {len(keys)} passages already have text; "
                f"{len(todo)} to go."
            )

            if options["dry_run"]:
                continue

            if not options["fetch"]:
                # Placeholder rows: NULL fetched_at reads as "known but never retrieved",
                # which is exactly what the refresh task selects on.
                PassageText.objects.bulk_create(
                    [PassageText(passage_key=k, language=language) for k in todo],
                    batch_size=500, ignore_conflicts=True,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  queued {len(todo)} {language} passage(s) for the refresh task."
                    )
                )
                continue

            shared = prepare_shared_resources()
            retrieved = failed = 0
            for key in todo:
                if fetch_passage_text(key, citations[key], langs=[language], **shared).get(language):
                    retrieved += 1
                else:
                    failed += 1
            self.stdout.write(
                self.style.SUCCESS(f"  {language}: retrieved {retrieved}, failed {failed}.")
            )
