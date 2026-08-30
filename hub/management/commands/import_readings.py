"""Generates seed data for app with python manage.py seed."""
from datetime import date, datetime, timedelta
import logging

from django.core.management.base import BaseCommand

import hub.models as models
from hub.services.lectionary_service import get_daily_readings, persist_readings


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
            for reading_obj, created in persist_readings(day, readings):
                action = "Created" if created else "Updated"
                logging.info(f"{action} reading: {reading_obj}")
