"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime
import logging
import re

from rest_framework.exceptions import ValidationError
from rest_framework import generics
from rest_framework.response import Response

import urllib.request

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
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
            "<date>": {
                "<book_name>": {
                    "<chapter_number>": [
                        "<start_verse>",
                        "<end_verse>"
                    ]
                }
            }
        }

    Example Response:
        {
            "2024-03-11": {
                "Matthew": {
                    "5": ["1", "12"]
                },
                "Isaiah": {
                    "55": ["1", "13"]
                }
            }
        }
    """

    def get_cache_key(self, date):
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
            return Response({response_date: cached_readings})

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

        readings = {}
        book_start = html_content.find("<b>")
        
        while book_start != -1:
            i1 = book_start + len("<b>")
            i2 = html_content.find("</b>")
            reading_str = html_content[i1:i2]
    
            parser_regex = r"^([A-za-z\'\. ]+) ([0-9]+)\.([0-9]+)\-([0-9]+\.)?([0-9]+)$"
            groups = re.search(parser_regex, reading_str)

            # skip reading if does not match parser regex
            if groups is None:
                logging.error("Could not parse reading %s at %s with regex %s", reading_str, url, parser_regex)
                html_content = html_content[i2 + 1:]
                book_start = html_content.find("<b>")
                continue

            book = groups.group(1)
            chapter_start = groups.group(2)
            verse_start = groups.group(3)
            chapter_end = groups.group(4).strip(".") if groups.group(4) else chapter_start  # rm decimal from group
            verse_end = groups.group(5)
    
            readings[book] = [(chapter_start, verse_start), (chapter_end, verse_end)]
    
            html_content = html_content[i2 + len("</b>"):]
            book_start = html_content.find("<b>")

        # Cache the results for 24 hours (86400 seconds)
        cache.set(cache_key, readings, 86400)
        
        return Response({response_date: readings})
