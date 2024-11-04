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


class GetDailyReadingsForToday(generics.GenericAPIView):
    """
    API view to retrieve daily scripture readings by scraping sacredtradition.am.

    This view scrapes the Daily Worship app's website to get the daily readings
    for the current date. It parses the HTML content to extract book names,
    chapters, and verse ranges. Including the optional paramater 'date', it
    will return the readings for that date instead of today's date.

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

    def get(self, request, *args, **kwargs):
        if date_str := self.request.query_params.get('date'):
            try:
                date = datetime.strftime(datetime.strptime(date_str, "%Y-%m-%d").date(), "%Y%m%d")
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: yyyy-mm-dd.")
        else:
            date = datetime.strftime(datetime.today().date(), "%Y%m%d")

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

        return Response(readings)
