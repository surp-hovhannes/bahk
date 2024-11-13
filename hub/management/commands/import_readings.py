"""Generates seed data for app with python manage.py seed."""
from datetime import date, datetime, timedelta
import logging

from django.core.management.base import BaseCommand

import hub.models as models
from hub.utils import scrape_readings


def daterange(start_date: date, end_date: date):
    days = int((end_date - start_date).days)
    for n in range(days):
        yield start_date + timedelta(n)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--church", required=True, help="name of church to add reading to their calendar")
        parser.add_argument('--start_date', default=date.today().strftime("%Y-%m-%d"), help="date to start importing readings")
        parser.add_argument('--end_date', default=(date.today() + timedelta(10)).strftime("%Y-%m-%d"),
                            help="date to end importing readings")

    def handle(self, *args, **options):
        try:
            church = models.Church.objects.get(name=options["church"])
        except models.Church.DoesNotExist:
            logging.error("Church %s does not exist. No readings imported.", options["church"])
            return

        start_date = datetime.strptime(options["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(options["end_date"], "%Y-%m-%d")
        for date_obj in daterange(start_date, end_date):
            day, _ = models.Day.objects.get_or_create(church=church, date=date_obj)
            readings = scrape_readings(date_obj, church)
            for reading in readings:
                reading.update({"day": day})
                models.Reading.objects.get_or_create(**reading)
