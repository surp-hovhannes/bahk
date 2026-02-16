"""Fetch Armenian Bible text from sacredtradition.am for readings missing text_hy."""
import logging

from django.core.management.base import BaseCommand

from hub.models import Reading
from hub.tasks import fetch_armenian_reading_text_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fetch Armenian Bible text from sacredtradition.am for readings that are missing text_hy"

    def add_arguments(self, parser):
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Queue each reading as a Celery task instead of running synchronously",
        )

    def handle(self, *args, **options):
        all_readings = Reading.objects.select_related("day", "day__church").all()
        total_count = all_readings.count()
        missing_ids = [r.id for r in all_readings if not r.text_hy]
        missing_count = len(missing_ids)

        self.stdout.write(
            f"Readings without Armenian text: {missing_count}/{total_count}"
        )

        if missing_count == 0:
            self.stdout.write(self.style.SUCCESS("All readings already have Armenian text."))
            return

        if options["run_async"]:
            for reading_id in missing_ids:
                fetch_armenian_reading_text_task.delay(reading_id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queued {missing_count} fetch_armenian_reading_text_task(s) via Celery."
                )
            )
        else:
            self.stdout.write("Running synchronously (this may take a while)...")
            success = 0
            failed = 0
            for reading_id in missing_ids:
                try:
                    fetch_armenian_reading_text_task(reading_id)
                    reading = Reading.objects.get(pk=reading_id)
                    if reading.text_hy:
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error("Error fetching Armenian text for reading %s: %s", reading_id, e)
                    failed += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. Fetched: {success}, Failed/no match: {failed}."
                )
            )
