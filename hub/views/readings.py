"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime
import logging

from rest_framework.exceptions import ValidationError
from rest_framework import generics
from rest_framework.response import Response

from hub.models import Church, Day, Reading
from hub.utils import scrape_readings
import bahk.settings as settings


class GetDailyReadingsForDate(generics.GenericAPIView):
    """
    API view to provide daily Scripture readings from database along with link to read them.

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
                    "endVerse": 10,
                    "url": https://link.to.read.this.passage/
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
                    "endVerse": 12,
                    "url": "https://catenabible.com/mt/5"
                },
                {
                    "book": "Isaiah",
                    "startChapter": 55,
                    "startVerse": 1,
                    "endChapter": 55,
                    "endVerse": 13,
                    "url": "https://catenabible.com/is/55"
                }
            ]
        }
    """
    queryset = Reading.objects.all()

    def get(self, request, *args, **kwargs):
        date_format = "%Y-%m-%d"

        if date_str := self.request.query_params.get('date', datetime.today().strftime(date_format)):
            try:
                date_obj = datetime.strptime(date_str, date_format).date()
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: YYYY-MM-DD")
        else:
            date_obj = datetime.today().date()

        if request.user.is_authenticated:
            church = request.user.profile.church
        else:
            church = Church.objects.get(pk=Church.get_default_pk())

        day, _ = Day.objects.get_or_create(date=date_obj, church=church)

        formatted_readings = []
        if not day.readings.exists():
            # import readings for this date into db
            readings = scrape_readings(date_obj)
            for reading in readings:
                reading.update({"day": day})
                Reading.objects.get_or_create(**reading)

        for reading in day.readings.all():
            # format in lower camel case to match JavaScript variable naming
            formatted_readings.append({
                "book": reading.book,
                "startChapter": reading.start_chapter,
                "startVerse": reading.start_verse,
                "endChapter": reading.end_chapter,
                "endVerse": reading.end_verse,
                "url": reading.create_url()
            })

        # Create the final response format
        response_data = {
            "date": date_str,
            "readings": formatted_readings,
        }

        return Response(response_data)
