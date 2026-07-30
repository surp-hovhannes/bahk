"""Generates seed data for app with python manage.py seed."""
from datetime import date, datetime, timedelta
import logging

from django.core.management.base import BaseCommand

import hub.models as models
from hub.services.lectionary_service import get_daily_readings
from hub.services.reading_text_service import book_hy_for_book


def daterange(start_date: date, end_date: date):
    days = int((end_date - start_date).days)
    for n in range(days):
        yield start_date + timedelta(n)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--church", required=True, help="name of church to add reading to their calendar")
        parser.add_argument('--start_date', default=None, help="date to start importing readings")
        parser.add_argument('--end_date', default=None,
                            help="date to end importing readings")

    def handle(self, *args, **options):
        try:
            church = models.Church.objects.get(name=options["church"])
        except models.Church.DoesNotExist:
            logging.error("Church %s does not exist. No readings imported.", options["church"])
            return

        today = date.today()
        start_date_value = options["start_date"] or today.strftime("%Y-%m-%d")
        end_date_value = options["end_date"] or (today + timedelta(10)).strftime("%Y-%m-%d")

        start_date = datetime.strptime(start_date_value, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_value, "%Y-%m-%d")
        for date_obj in daterange(start_date, end_date):
            day, _ = models.Day.objects.get_or_create(church=church, date=date_obj)
            readings = get_daily_readings(date_obj, church)
            for reading in readings:
                reading.update({"day": day})
                # Extract and remove all book-related fields to handle them separately
                book_en = reading.pop("book_en", reading.get("book"))
                # get_daily_readings() never returns "book_hy" (it's not part of the engine's
                # output shape); resolve it ourselves from the same usfm_mapping.json that
                # fetch_armenian_text() uses, so it's populated at import time rather than
                # left empty until a later text-fetch backfill (see PR #461 review).
                book_hy = book_hy_for_book(book_en)
                # Remove 'book' from the dict to avoid using it in get_or_create lookup
                reading.pop("book", None)

                # Use explicit lookup with book_en to match the uniqueness constraint
                # (modeltrans treats 'book' as 'book_en' in the database)
                reading_obj, created = models.Reading.objects.get_or_create(
                    day=reading["day"],
                    book=book_en,  # This becomes book_en in the database
                    start_chapter=reading["start_chapter"],
                    start_verse=reading["start_verse"],
                    end_chapter=reading["end_chapter"],
                    end_verse=reading["end_verse"]
                )

                # Update translations if they are missing
                if book_hy and not reading_obj.book_hy:
                    reading_obj.book_hy = book_hy
                    reading_obj.save(update_fields=['i18n'])
                    action = "Created" if created else "Updated"
                    logging.info(f"{action} reading with translations: {reading_obj}")
