"""Generates seed data for app with python manage.py seed."""
from datetime import date, datetime, timedelta
import logging
import re

import urllib
from django.core.management.base import BaseCommand

import hub.models as models


MAX_NUM_READINGS = 40
PARSER_REGEX = r"^([A-za-z1-4\'\. ]+) ([0-9]+\.)?([0-9]+)\-?([0-9]+\.)?([0-9]+)?$"


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
            
            ct = 0
            while book_start != -1:
                # prevent infinite loop
                if ct > MAX_NUM_READINGS:
                    logging.error("Reached maximum number of readings. Breaking to avoid infinite loop.")
                    break
                ct += 1

                i1 = book_start + len("<b>")
                i2 = html_content.find("</b>")
                reading_str = html_content[i1:i2]

                # advance to next section of web text early to prevent infinite loop in case of failure later on
                html_content = html_content[i2 + 1:]
                book_start = html_content.find("<b>")

                if "," in reading_str:
                    # TODO: if comma found, second reading appended to first, as for Daniel 3.1-23 and Azariah 1-68
                    # for now, omit second reading
                    reading_str = reading_str.split(",")[0]
                
                groups = re.search(PARSER_REGEX, reading_str)

                # skip reading if does not match parser regex
                if groups is None:
                    logging.error("Could not parse reading %s at %s with regex %s", reading_str, url, PARSER_REGEX)
                    continue

                try:
                    # parse groups
                    book = groups.group(1)
                    # remove decimal if start chapter provided; otherwise, part of book with 1 chapter
                    start_chapter = groups.group(2).strip(".") if groups.group(2) is not None else 1
                    start_verse = groups.group(3)
                    # remove decimal if end chapter provided; otherwise, must be the same as the start chapter
                    end_chapter = groups.group(4).strip(".") if groups.group(4) is not None else start_chapter
                    end_verse = groups.group(5) if groups.group(5) is not None else start_verse

                    day, _ = models.Day.objects.get_or_create(church=church, date=single_date)
                    models.Reading.objects.get_or_create(
                        day=day,
                        book=book,
                        start_chapter=int(start_chapter),
                        start_verse=int(start_verse),
                        end_chapter=int(end_chapter),
                        end_verse=int(end_verse),
                    )
                except Exception:
                    logging.error(
                        "Could not parse reading with text %s with regex %s from %s. Skipping.", 
                        reading_str, PARSER_REGEX, url, exc_info=True
                    )
                    continue
