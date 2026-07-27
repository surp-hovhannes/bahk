"""Compose Armenian text for passages missing it, from the offline BibleVerse corpus."""
import logging

from django.core.management.base import BaseCommand

from hub.models import PassageText, Reading
from hub.services.reading_text_service import fetch_passage_text

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Compose Armenian Bible text for passages that have none, from the offline "
        "BibleVerse corpus. Work is counted in distinct passages, not readings: text is "
        "stored per passage, so one composition serves every date citing it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Queue each passage as a Celery task instead of running synchronously",
        )

    def handle(self, *args, **options):
        # One representative reading per passage: composing for any of them stores the
        # text against the passage, satisfying all the others.
        representatives: dict[str, tuple] = {}
        rows = (
            Reading.objects.exclude(passage_key="")
            .values_list("passage_key", "id", "book", "start_chapter", "start_verse",
                         "end_chapter", "end_verse")
            .iterator(chunk_size=1000)
        )
        for key, reading_id, *citation in rows:
            representatives.setdefault(key, (reading_id, tuple(citation)))

        have = set(
            PassageText.objects.filter(language="hy")
            .exclude(text="")
            .values_list("passage_key", flat=True)
        )
        missing = {k: v for k, v in representatives.items() if k not in have}

        self.stdout.write(
            f"Passages without Armenian text: {len(missing)}/{len(representatives)}"
        )
        if not missing:
            self.stdout.write(self.style.SUCCESS("All passages already have Armenian text."))
            return

        if options["run_async"]:
            from hub.tasks import fetch_armenian_reading_text_task

            for reading_id, _citation in missing.values():
                fetch_armenian_reading_text_task.delay(reading_id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queued {len(missing)} fetch_armenian_reading_text_task(s) via Celery."
                )
            )
            return

        # Composition is local (indexed range scans over BibleVerse), so there is no quota
        # to respect and no reason to pace the loop.
        self.stdout.write("Composing from the local corpus...")
        success = failed = 0
        for key, (_reading_id, citation) in missing.items():
            try:
                if fetch_passage_text(key, citation, langs=["hy"]).get("hy"):
                    success += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error("Error composing Armenian text for %s: %s", key, exc)
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done. Composed: {success}, not in corpus: {failed}.")
        )
