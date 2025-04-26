"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime
import logging

from rest_framework.exceptions import ValidationError
from rest_framework import generics
from rest_framework.response import Response
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.views import APIView
from hub.tasks import generate_reading_context_task

from hub.models import Church, Day, Reading
from hub.utils import scrape_readings


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

        # If no readings exist for the requested day/church, scrape and persist them
        if not day.readings.exists():
            # import readings for this date into db
            readings = scrape_readings(date_obj, church)
            for reading in readings:
                reading.update({"day": day})
                Reading.objects.get_or_create(**reading)

        # Now ensure we have up-to-date queryset
        day.refresh_from_db()

        for reading in day.readings.all():
            # Trigger context generation if missing
            if not reading.context:
                logging.info("Enqueue context generation for reading %s", reading.id)
                generate_reading_context_task.delay(reading.id)

        formatted_readings = []
        for reading in day.readings.all():
            formatted_readings.append({
                "id": reading.id,
                "book": reading.book,
                "startChapter": reading.start_chapter,
                "startVerse": reading.start_verse,
                "endChapter": reading.end_chapter,
                "endVerse": reading.end_verse,
                "url": reading.create_url(),
                "context": reading.context,
                "context_thumbs_up": reading.context_thumbs_up,
                "context_thumbs_down": reading.context_thumbs_down,
            })

        response_data = {
            "date": date_str,
            "readings": formatted_readings,
        }

        return Response(response_data)

# New Feedback view
class ReadingContextFeedbackView(APIView):
    """
    API view to handle user feedback (thumbs up / thumbs down) for the AI-generated
    context text of a `Reading`.

    Permissions:
        - AllowAny (adjust as needed, e.g. `IsAuthenticated` if you only want
          logged-in users to submit feedback)

    Path Parameters (URL):
        pk (int): Primary key of the `Reading` object.

    Request Body (JSON):
        {
            "feedback_type": "up"   # valid values: "up" or "down"
        }

    Behaviour:
        • If `feedback_type` == "up" – increments `context_thumbs_up`.
        • If `feedback_type` == "down" – increments `context_thumbs_down`.
        • When downs reach the configurable threshold
          (`settings.READING_CONTEXT_REGENERATION_THRESHOLD`, default **5**)
          a new Celery task (`generate_reading_context_task`) is enqueued to
          regenerate the context.  Vote counters remain stored so front-end can
          still display them until the new context is generated (the task will
          reset counts on success).

    Responses:
        200 OK – JSON object `{ "status": "success", "regenerate": bool }`
                  where `regenerate` is *true* only when regeneration is
                  triggered (on a down-vote).
        400 Bad Request – when an invalid `feedback_type` is supplied.
        404 Not Found – when the supplied `pk` does not correspond to a Reading.
    """
    def post(self, request, pk):
        reading = get_object_or_404(Reading, pk=pk)
        feedback_type = request.data.get('feedback_type')
        if feedback_type == 'up':
            reading.context_thumbs_up += 1
            reading.save(update_fields=['context_thumbs_up'])
            return Response({"status": "success", "regenerate": False})
        elif feedback_type == 'down':
            reading.context_thumbs_down += 1
            threshold = getattr(settings, 'READING_CONTEXT_REGENERATION_THRESHOLD', 5)
            regenerate = False
            if reading.context_thumbs_down >= threshold:
                regenerate = True
                # Force regeneration via Celery task
                generate_reading_context_task.delay(reading.id, force_regeneration=True)
            reading.save(update_fields=['context_thumbs_down'])
            return Response({"status": "success", "regenerate": regenerate})
        else:
            return Response({"status": "error", "message": "Invalid feedback type"}, status=status.HTTP_400_BAD_REQUEST)
