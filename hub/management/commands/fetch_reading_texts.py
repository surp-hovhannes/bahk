"""Fetch Bible text from API.Bible for readings missing text."""
import logging

from django.core.management.base import BaseCommand

from hub.models import Reading
from hub.tasks import refresh_all_reading_texts_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fetch Bible text from API.Bible for readings that are missing or stale"

    def add_arguments(self, parser):
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Queue as a Celery task instead of running synchronously",
        )

    def handle(self, *args, **options):
        missing_count = Reading.objects.filter(text="").count()
        total_count = Reading.objects.count()

        self.stdout.write(
            f"Readings without text: {missing_count}/{total_count}"
        )

        if missing_count == 0:
            self.stdout.write(self.style.SUCCESS("All readings already have text."))
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
