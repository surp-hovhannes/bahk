"""Report Scripture-text coverage and run the refresh task."""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from hub.models import PassageText, Reading
from hub.services.reading_text_service import (
    TEXT_FETCHERS,
    stale_passage_text_queryset,
)
from hub.tasks import refresh_all_reading_texts_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Retrieve Scripture text for passages that are missing or stale. Work is counted "
        "in distinct passages, not readings: text is stored per passage, so one retrieval "
        "serves every date citing it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Queue as a Celery task instead of running synchronously",
        )

    def handle(self, *args, **options):
        refresh_days = getattr(settings, "READING_TEXT_REFRESH_DAYS", 23)
        limit = getattr(settings, "READING_REFRESH_LIMIT", 1500)

        total_readings = Reading.objects.count()
        keys_in_use = set(
            Reading.objects.exclude(passage_key="")
            .values_list("passage_key", flat=True)
            .distinct()
        )
        unmappable = Reading.objects.filter(passage_key="").count()

        if not keys_in_use:
            self.stdout.write(self.style.WARNING("No readings with a resolvable passage."))
            return

        self.stdout.write(
            f"{total_readings} readings resolve to {len(keys_in_use)} distinct passages "
            f"({total_readings / len(keys_in_use):.1f}x dedup)."
        )
        if unmappable:
            self.stdout.write(
                self.style.WARNING(
                    f"{unmappable} readings have no USFM mapping and are never retrieved."
                )
            )

        any_stale = False
        for language in TEXT_FETCHERS:
            known = set(
                PassageText.objects.filter(language=language, passage_key__in=keys_in_use)
                .values_list("passage_key", flat=True)
            )
            stale = set(
                stale_passage_text_queryset(language, refresh_days)
                .filter(passage_key__in=keys_in_use)
                .values_list("passage_key", flat=True)
            )
            stale_keys = stale | (keys_in_use - known)
            if not stale_keys:
                self.stdout.write(f"  {language}: all {len(keys_in_use)} passages fresh.")
                continue
            any_stale = True
            will_refresh = min(len(stale_keys), limit)
            self.stdout.write(
                f"  {language}: {len(stale_keys)} stale (>{refresh_days}d or never "
                f"retrieved); this run will retrieve {will_refresh} (limit {limit})"
                + (f", {len(stale_keys) - will_refresh} left over." if len(stale_keys) > limit else ".")
            )

        if not any_stale:
            self.stdout.write(self.style.SUCCESS("All passages already have fresh text."))
            return

        if options["run_async"]:
            refresh_all_reading_texts_task.delay()
            self.stdout.write(
                self.style.SUCCESS("Queued refresh_all_reading_texts_task via Celery.")
            )
        else:
            self.stdout.write("Running synchronously (this may take a while)...")
            refresh_all_reading_texts_task()
            self.stdout.write(self.style.SUCCESS("Done."))
