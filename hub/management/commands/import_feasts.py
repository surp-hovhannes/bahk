"""Import feast days from sacredtradition.am."""
from datetime import date, datetime, timedelta
import logging

from django.core.management.base import BaseCommand

import hub.models as models
from hub.utils import scrape_feast


def daterange(start_date: date, end_date: date):
    days = int((end_date - start_date).days)
    for n in range(days):
        yield start_date + timedelta(n)


class Command(BaseCommand):
    help = 'Import feast days from sacredtradition.am for a given date range'

    def add_arguments(self, parser):
        parser.add_argument("--church", required=True, help="name of church to add feast to their calendar")
        parser.add_argument('--start_date', default=date.today().strftime("%Y-%m-%d"), help="date to start importing feasts")
        parser.add_argument('--end_date', default=(date.today() + timedelta(10)).strftime("%Y-%m-%d"),
                            help="date to end importing feasts")

    def handle(self, *args, **options):
        try:
            church = models.Church.objects.get(name=options["church"])
        except models.Church.DoesNotExist:
            logging.error("Church %s does not exist. No feasts imported.", options["church"])
            return

        start_date = datetime.strptime(options["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(options["end_date"], "%Y-%m-%d")

        feasts_created = 0
        feasts_updated = 0

        for date_obj in daterange(start_date, end_date):
            feast_data = scrape_feast(date_obj, church)

            # Skip if no feast found for this date
            if not feast_data:
                logging.debug(f"No feast found for {date_obj.strftime('%Y-%m-%d')}")
                continue

            # Extract and remove all name-related fields to handle them separately
            name_en = feast_data.get("name_en") or feast_data.get("name")
            name_hy = feast_data.get("name_hy")

            if not name_en:
                logging.warning(f"No feast name found for {date_obj.strftime('%Y-%m-%d')}, skipping")
                continue

            # Get or create the feast
            feast_obj, created = models.Feast.objects.get_or_create(
                date=date_obj,
                church=church,
                defaults={"name": name_en}
            )

            # Update translations if they are missing
            if name_hy and not feast_obj.name_hy:
                feast_obj.name_hy = name_hy
                feast_obj.save(update_fields=['i18n'])
                action = "Created" if created else "Updated"
                logging.info(f"{action} feast with translations: {feast_obj}")

            if created:
                feasts_created += 1
            elif name_hy:
                feasts_updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully imported feasts for {options['church']}. "
            f"Created: {feasts_created}, Updated: {feasts_updated}"
        ))
