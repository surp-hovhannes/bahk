"""Generates seed data for app with python manage.py seed."""
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
        for date_obj in daterange(start_date, end_date):
            feast_data = scrape_feast(date_obj, church)
            if not feast_data:
                logging.warning("No feast data found for date %s. Skipping.", date_obj.strftime("%Y-%m-%d"))
                continue

            # Extract and remove all name-related fields to handle them separately
            name_en = feast_data.pop("name_en", feast_data.get("name"))
            name_hy = feast_data.pop("name_hy", None)
            # Remove 'name' from the dict to avoid using it in get_or_create lookup
            feast_data.pop("name", None)

            # Use explicit lookup with name_en to match the uniqueness constraint
            # (modeltrans treats 'name' as 'name_en' in the database)
            feast_obj, created = models.Feast.objects.get_or_create(
                date=date_obj,
                church=church,
                defaults={"name": name_en}
            )

            # Update translations if they are missing or if the name has changed
            if name_en and feast_obj.name != name_en:
                feast_obj.name = name_en
                feast_obj.save(update_fields=['name'])
            
            if name_hy and not feast_obj.name_hy:
                feast_obj.name_hy = name_hy
                feast_obj.save(update_fields=['i18n'])
                action = "Created" if created else "Updated"
                logging.info(f"{action} feast with translations: {feast_obj}")
