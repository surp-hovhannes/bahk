"""Import feasts for a date range."""
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
    help = "Import feast data for a date range"

    def add_arguments(self, parser):
        parser.add_argument("--church", required=True, help="name of church to add feasts to their calendar")
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
            day, _ = models.Day.objects.get_or_create(church=church, date=date_obj)
            feast_data = scrape_feast(date_obj, church)
            
            if feast_data:
                # Extract name fields
                name_en = feast_data.get("name_en", feast_data.get("name"))
                name_hy = feast_data.get("name_hy", None)
                
                if not name_en:
                    # If no English name, try to use name field directly
                    name_en = feast_data.get("name")
                
                if not name_en:
                    logging.warning(f"No feast name found for {date_obj}. Skipping.")
                    continue
                
                # Get or create feast with English name
                feast_obj, created = models.Feast.objects.get_or_create(
                    day=day,
                    defaults={"name": name_en}
                )
                
                # Set translation if available and missing
                # For new feasts, set it immediately after creation to avoid second save
                # For existing feasts, only update if translation is missing
                if name_hy and not feast_obj.name_hy:
                    feast_obj.name_hy = name_hy
                    # Only save if feast was just created (to set translation) 
                    # or if it existed and needs translation update
                    if created:
                        # For new feasts, save immediately after setting translation
                        # This triggers post_save once with both name and translation set
                        feast_obj.save()
                    else:
                        # For existing feasts, save with update_fields to only update i18n
                        feast_obj.save(update_fields=['i18n'])
                
                action = "Created" if created else "Updated"
                logging.info(f"{action} feast: {feast_obj}")
            else:
                logging.debug(f"No feast found for {date_obj}")
