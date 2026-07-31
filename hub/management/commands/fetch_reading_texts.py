"""Fetch Bible text from API.Bible for readings missing text."""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from hub.models import Reading
from hub.services.reading_text_service import (
    select_nearest_stale_reading_ids,
    stale_reading_queryset,
)
from hub.tasks import refresh_all_reading_texts_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Fetch Bible text from API.Bible for readings that are missing or stale. "
        "Each run refreshes at most READING_REFRESH_LIMIT readings, nearest to today first."
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
        limit = getattr(settings, "READING_REFRESH_LIMIT", 2000)

        stale_readings = stale_reading_queryset(refresh_days)
        missing_count = Reading.objects.filter(text="").count()
        stale_count = stale_readings.count()
        total_count = Reading.objects.count()

        self.stdout.write(
            f"Readings without text: {missing_count}/{total_count}; "
            f"stale (>{refresh_days}d): {stale_count}/{total_count}"
        )

        if stale_count == 0:
            self.stdout.write(self.style.SUCCESS("All readings already have fresh text."))
            return

        # Use the same selection the task will, so the reported number cannot drift.
        will_refresh = len(select_nearest_stale_reading_ids(limit, queryset=stale_readings))
        self.stdout.write(
            f"This run will refresh {will_refresh} readings "
            f"(limit {limit}, nearest to today first); "
            f"{max(stale_count - will_refresh, 0)} will remain stale."
        )

        if options["run_async"]:
            refresh_all_reading_texts_task.delay()
            self.stdout.write(
                self.style.SUCCESS("Queued refresh_all_reading_texts_task via Celery.")
            )
        else:
            self.stdout.write("Running synchronously (this may take a while)...")
            refresh_all_reading_texts_task()
            self.stdout.write(self.style.SUCCESS("Done."))
