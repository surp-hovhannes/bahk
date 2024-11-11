"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime

from rest_framework.exceptions import ValidationError
from rest_framework import generics
from rest_framework.response import Response

from hub.models import Day


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

    def get(self, request, *args, **kwargs):
        date_format = "%Y-%m-%d"

        if date_str := self.request.query_params.get('date', datetime.today().strftime(date_format)):
            try:
                date_obj = datetime.strptime(date_str, date_format).date()
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: YYYY-MM-DD")
        else:
            date_obj = datetime.today().date()

        day, _ = Day.objects.get_or_create(date=date_obj)
        formatted_readings = []
        for reading in day.readings.all():
            formatted_reading = {
                "book": reading.book,
                "start_chapter": reading.start_chapter,
                "start_verse": reading.start_verse,
                "end_chapter": reading.end_chapter,
                "end_verse": reading.end_verse,
                "url": reading.create_url()
            }
            formatted_readings.append(formatted_reading)

        # Create the final response format
        response_data = {
            "date": date_str,
            "readings": formatted_readings,
        }

        return Response(response_data)
