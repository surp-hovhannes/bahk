"""Generates seed data for app with python manage.py seed."""
from datetime import date, datetime, timedelta
import logging
import re

import urllib
from django.core.management.base import BaseCommand

import hub.models as models


def daterange(start_date: date, end_date: date):
    days = int((end_date - start_date).days)
    for n in range(days):
        yield start_date + timedelta(n)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--church", help="name of church to add reading to their calendar")
        parser.add_argument('--start_date', default=date.today().strftime("%Y-%m-%d"), help="date to start importing readings")
        parser.add_argument('--end_date', default=(date.today() + timedelta(10)).strftime("%Y-%m-%d"),
                            help="date to end importing readings")

    def handle(self, *args, **options):
        church, _ = models.Church.objects.get_or_create(name=options["church"])

        # Format required by sacredtradition.am (YYYYMMDD)
        scraper_date_format = "%Y%m%d"

        start_date = datetime.strptime(options["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(options["end_date"], "%Y-%m-%d")
        for single_date in daterange(start_date, end_date):

            scraper_date = single_date.strftime(scraper_date_format)

            url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM1103&iL=2&ymd={scraper_date}"
            try:
                response = urllib.request.urlopen(url)
            except urllib.error.URLError:
                logging.error("Invalid url %s", url)
                break

            if response.status != 200:
                logging.error("Could not access readings from url %s. Failed with status %r", url, response.status)
                break

            data = response.read()
            html_content = data.decode("utf-8")

            book_start = html_content.find("<b>")
            
            while book_start != -1:
                i1 = book_start + len("<b>")
                i2 = html_content.find("</b>")
                reading_str = html_content[i1:i2]
                # advance to next section of web text to prevent infinite loop
                html_content = html_content[i2 + 1:]
                book_start = html_content.find("<b>")

                if "," in reading_str:
                    # rare occasion where two readings are merged (Dnaniel 3.1-23 and Azariah 1-68)
                    reading, _ = models.Reading.objects.get_or_create(
                        day=day,
                        book="Azariah",
                        start_chapter=1,
                        start_verse=1,
                        end_chapter=1,
                        end_verse=68,
                    )
                    reading_str = reading_str.split(",")[0]

                parser_regex = r"^([A-za-z1-4\'\. ]+) ([0-9]+)\.([0-9]+)\-?([0-9]+\.)?([0-9]+)?$"
                groups = re.search(parser_regex, reading_str)

                # skip reading if does not match parser regex
                if groups is None:
                    logging.error("Could not parse reading %s at %s with regex %s", reading_str, url, parser_regex)
                    continue

                try:
                    # parse groups
                    book = groups.group(1)
                    start_chapter = groups.group(2)
                    start_verse = groups.group(3)
                    end_chapter = groups.group(4).strip(".") if groups.group(4) is not None else start_chapter  # rm decimal
                    end_verse = groups.group(5) if groups.group(5) is not None else start_verse

                    day, _ = models.Day.objects.get_or_create(church=church, date=single_date)
                    reading, _ = models.Reading.objects.get_or_create(
                        day=day,
                        book=book,
                        start_chapter=int(start_chapter),
                        start_verse=int(start_verse),
                        end_chapter=int(end_chapter),
                        end_verse=int(end_verse),
                    )
                except Exception as e:
                    logging.error("Could not parse reading with text %s with regex %s from %s. Skipping.", 
                                reading_str, parser_regex, url, exc_info=True)
                    continue
