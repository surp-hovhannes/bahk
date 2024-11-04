"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime
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
        - date (str): Optional. The date to get the readings for in the format YYYYMMDD.

    Returns:
        - A JSON response with the following structure:
        {
            "<book_name>": {
                "<chapter_number>": [
                    "<start_verse>",
                    "<end_verse>"
                ]
            },
            ...
        }

    Example Response:
        {
            "Matthew": {
                "5": ["1", "12"]
            },
            "Isaiah": {
                "55": ["1", "13"]
            }
        }
    """

    def get_cache_key(self, date):
        return f"daily_readings_{date}"

    def get(self, request, *args, **kwargs):
        if date_str := self.request.query_params.get('date'):
            try:
                date = datetime.strftime(datetime.strptime(date_str, "%Y-%m-%d").date(), "%Y%m%d")
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: yyyy-mm-dd.")
        else:
            date = datetime.strftime(datetime.today().date(), "%Y%m%d")

        # Try to get from cache first
        cache_key = self.get_cache_key(date)
        cached_readings = cache.get(cache_key)
        if cached_readings:
            return Response(cached_readings)

        # If not in cache, fetch and store
        url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM1103&iL=2&ymd={date}"
        response = urllib.request.urlopen(url)
        data = response.read()
        html_content = data.decode("utf-8")

        readings = {}
        book_start = html_content.find("<b>")
        
        while book_start != -1:
            i1 = book_start + len("<b>")
            i2 = html_content.find("</b>")
            reading_str = html_content[i1:i2]
    
            groups = re.search("^([A-za-z\'\. ]+) ([0-9]+)\.([0-9]+)\-([0-9]+)$", reading_str)

            book = groups.group(1)
            chapter = groups.group(2)
            verse_start = groups.group(3)
            verse_end = groups.group(4)
    
            readings[book] = {chapter: [verse_start, verse_end]}
    
            html_content = html_content[i2 + 1:]
            book_start = html_content.find("<b>")

        # Cache the results for 24 hours (86400 seconds)
        cache.set(cache_key, readings, 86400)
        
        return Response(readings)
