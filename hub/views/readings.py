"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime
import logging
import re
import urllib.request

from rest_framework.exceptions import ValidationError
from rest_framework import generics
from rest_framework.response import Response

from django.core.cache import cache


class GetDailyReadingsForDate(generics.GenericAPIView):
    """
    API view to retrieve daily scripture readings by scraping sacredtradition.am.
    Results are cached for 24 hours per unique date requested.

    Permissions:
        - AllowAny: No authentication required

    Query Parameters:
        - date (str): Optional. The date to get the readings for in the format YYYY-MM-DD.

    Returns:
        - A JSON response with the following structure:
        {
            "date": "YYYY-MM-DD",
            "readings": [
                {
                    "book": "Book Name",
                    "startChapter": 1,
                    "startVerse": 1,
                    "endChapter": 1,
                    "endVerse": 10
                }
            ]
        }

    Example Response:
        {
            "date": "2024-03-11",
            "readings": [
                {
                    "book": "Matthew",
                    "startChapter": 5,
                    "startVerse": 1,
                    "endChapter": 5,
                    "endVerse": 12
                },
                {
                    "book": "Isaiah",
                    "startChapter": 55,
                    "startVerse": 1,
                    "endChapter": 55,
                    "endVerse": 13
                }
            ]
        }
    """

    def get_cache_key(self, date):
        """
        Generate a cache key for the daily readings.
        """
        return f"daily_readings_{date}"

    def get(self, request, *args, **kwargs):
        # Format for API response (YYYY-MM-DD)
        response_date_format = "%Y-%m-%d"
        # Format required by sacredtradition.am (YYYYMMDD)
        scraper_date_format = "%Y%m%d"

        if date_str := self.request.query_params.get('date'):
            try:
                date_obj = datetime.strptime(date_str, response_date_format).date()
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: YYYY-MM-DD")
        else:
            date_obj = datetime.today().date()

        response_date = date_obj.strftime(response_date_format)
        scraper_date = date_obj.strftime(scraper_date_format)

        # Try to get from cache first
        cache_key = self.get_cache_key(response_date)
        cached_readings = cache.get(cache_key)
        if cached_readings:
            return Response({
                "date": response_date,
                "readings": cached_readings
            })

        # If not in cache, fetch and store
        url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM1103&iL=2&ymd={scraper_date}"
        try:
            response = urllib.request.urlopen(url)
        except urllib.error.URLError:
            logging.error("Invalid url %s", url)
            return Response({})

        if response.status != 200:
            logging.error("Could not access readings from url %s. Failed with status %r", url, response.status)
            return Response({})

        data = response.read()
        html_content = data.decode("utf-8")

        formatted_readings = []
        book_start = html_content.find("<b>")
        
        while book_start != -1:
            i1 = book_start + len("<b>")
            i2 = html_content.find("</b>")
            reading_str = html_content[i1:i2]
            # advance to next section of web text to prevent infinite loop
            html_content = html_content[i2 + 1:]
            book_start = html_content.find("<b>")
    
            parser_regex = r"^([A-za-z\'\. ]+) ([0-9]+)\.([0-9]+)\-?([0-9]+\.)?([0-9]+)?$"
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
    
                # Instead of building the old dictionary format, create a reading object
                reading = {
                    "book": book,
                    "startChapter": int(start_chapter),  # Convert to integers
                    "startVerse": int(start_verse), 
                    "endChapter": int(end_chapter), 
                    "endVerse": int(end_verse) 
                }
            except Exception as e:
                logging.error("Could not parse reading with text %s with regex %s from %s. Skipping.", 
                              reading_str, parser_regex, url)
                continue

            formatted_readings.append(reading)

        # Create the final response format
        response_data = {
            "date": response_date,
            "readings": formatted_readings
        }

        # Cache the results for 24 hours (86400 seconds)
        cache.set(cache_key, formatted_readings, 86400)
        
        return Response(response_data)
