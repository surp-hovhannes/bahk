"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""

import logging
from datetime import datetime

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.translation import activate, get_language_from_request
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.models import Church, Day, Reading
from hub.services.bible_api_service import BibleAPIService, fetch_text_for_reading
from hub.tasks import generate_reading_context_task
from hub.utils import get_user_profile_safe, scrape_readings


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
                    "id": 1,
                    "book": "Book Name",
                    "startChapter": 1,
                    "startVerse": 1,
                    "endChapter": 1,
                    "endVerse": 10,
                    "url": https://link.to.read.this.passage/,
                    "context": "AI-generated context text for the reading"
                    "context_thumbs_up": 10,
                    "context_thumbs_down": 2
                }
            ]
        }

    Example Response:
        {
            "date": "2024-03-11",
            "readings": [
                {
                    "id": 1,
                    "book": "Matthew",
                    "startChapter": 5,
                    "startVerse": 1,
                    "endChapter": 5,
                    "endVerse": 12,
                    "url": "https://catenabible.com/mt/5",
                    "context": "AI-generated context text for the reading",
                    "context_thumbs_up": 10,
                    "context_thumbs_down": 2
                },
                {
                    "id": 2,
                    "book": "Isaiah",
                    "startChapter": 55,
                    "startVerse": 1,
                    "endChapter": 55,
                    "endVerse": 13,
                    "url": "https://catenabible.com/is/55",
                    "context": "AI-generated context text for the reading",
                    "context_thumbs_up": 10,
                    "context_thumbs_down": 2
                }
            ]
        }
    """

    queryset = Reading.objects.all()

    def get(self, request, *args, **kwargs):
        date_format = "%Y-%m-%d"

        # Get and activate requested language
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)

        if date_str := self.request.query_params.get(
            "date", datetime.today().strftime(date_format)
        ):
            try:
                date_obj = datetime.strptime(date_str, date_format).date()
            except ValueError:
                raise ValidationError(
                    "Invalid date format. Expected format: YYYY-MM-DD"
                )
        else:
            date_obj = datetime.today().date()

        if request.user.is_authenticated:
            profile = get_user_profile_safe(request.user)
            church = profile.church if profile else Church.objects.get(pk=Church.get_default_pk())
        else:
            church = Church.objects.get(pk=Church.get_default_pk())

        day, _ = Day.objects.get_or_create(date=date_obj, church=church)

        # If no readings exist for the requested day/church, scrape and persist them
        if not day.readings.exists():
            # import readings for this date into db
            readings = scrape_readings(date_obj, church)
            new_reading_objs = []
            for reading in readings:
                reading.update({"day": day})
                # Extract and remove all book-related fields to handle them separately
                book_en = reading.pop("book_en", reading.get("book"))
                book_hy = reading.pop("book_hy", None)
                # Remove 'book' from the dict to avoid using it in get_or_create lookup
                reading.pop("book", None)

                # Use explicit lookup with book_en to match the uniqueness constraint
                # (modeltrans treats 'book' as 'book_en' in the database)
                reading_obj, created = Reading.objects.get_or_create(
                    day=reading["day"],
                    book=book_en,  # This becomes book_en in the database
                    start_chapter=reading["start_chapter"],
                    start_verse=reading["start_verse"],
                    end_chapter=reading["end_chapter"],
                    end_verse=reading["end_verse"]
                )

                # Set translations if they are missing
                if book_hy and not reading_obj.book_hy:
                    reading_obj.book_hy = book_hy
                    reading_obj.save(update_fields=['i18n'])

                # Track newly created readings that need text fetched
                if created and not reading_obj.text:
                    new_reading_objs.append(reading_obj)

            # Fetch Bible text synchronously for all new readings so text is
            # available in the response immediately (no Celery round-trip).
            # A single BibleAPIService instance is reused for all readings to
            # share the HTTP session and avoid repeated key lookups.
            if new_reading_objs:
                try:
                    service = BibleAPIService()
                    for reading_obj in new_reading_objs:
                        fetch_text_for_reading(reading_obj, service=service)
                except ValueError:
                    # API key not configured; text will remain empty until the
                    # periodic Celery refresh task runs.
                    logging.warning(
                        "BIBLE_API_KEY not configured; skipping synchronous text fetch for %d reading(s).",
                        len(new_reading_objs),
                    )

        # Now ensure we have up-to-date queryset
        day.refresh_from_db()

        formatted_readings = []
        for reading in day.readings.all():
            # Get translated book name
            book_translated = getattr(reading, 'book_i18n', reading.book)

            # Check if context exists and has all translations
            active_context = reading.active_context
            if active_context is None:
                # No context at all, trigger generation for all languages
                logging.warning("No context found for reading %s", str(reading))
                logging.info("Enqueue context generation for reading %s (all languages)", reading.id)
                generate_reading_context_task.delay(reading.id)
                context_dict = {
                    "context": "",
                    "context_thumbs_up": 0,
                    "context_thumbs_down": 0,
                }
            else:
                # Get the requested language translation
                context_text = getattr(active_context, 'text_i18n', active_context.text)

                # Check if all languages have translations
                from django.conf import settings
                available_languages = getattr(settings, 'MODELTRANS_AVAILABLE_LANGUAGES', ['en', 'hy'])
                all_languages_present = True
                for available_lang in available_languages:
                    if available_lang == 'en':
                        lang_text = active_context.text
                    else:
                        lang_text = getattr(active_context, f'text_{available_lang}', None)

                    if not lang_text or not lang_text.strip():
                        all_languages_present = False
                        break

                # If any translation is missing, trigger generation for all languages
                if not all_languages_present:
                    logging.info(
                        "Context translations missing for reading %s, enqueuing generation for all languages",
                        reading.id
                    )
                    generate_reading_context_task.delay(reading.id)

                context_dict = {
                    "context": context_text or "",
                    "context_thumbs_up": active_context.thumbs_up,
                    "context_thumbs_down": active_context.thumbs_down,
                }

            # Get language-specific text, version, copyright, and FUMS token
            if lang == 'hy':
                text_value = reading.text_hy or ""
                text_version = reading.text_hy_version or ""
                text_copyright = reading.text_hy_copyright or ""
                fums_token = reading.text_hy_fums_token or ""
            else:
                text_value = reading.text or ""
                text_version = reading.text_version or ""
                text_copyright = reading.text_copyright or ""
                fums_token = reading.fums_token or ""

            formatted_readings.append(
                {
                    "id": reading.id,
                    "book": book_translated,
                    "startChapter": reading.start_chapter,
                    "startVerse": reading.start_verse,
                    "endChapter": reading.end_chapter,
                    "endVerse": reading.end_verse,
                    "url": reading.create_url(),
                    "text": text_value,
                    "textCopyright": text_copyright,
                    "textVersion": text_version,
                    "fumsToken": fums_token,
                    **context_dict,
                }
            )

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
        active_context = reading.active_context

        # Check if active context exists
        if active_context is None:
            # Trigger context generation if not already in progress
            generate_reading_context_task.delay(reading.id)
            return Response(
                {
                    "status": "error",
                    "message": "No context available for this reading. Context generation has been queued."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        feedback_type = request.data.get("feedback_type")
        if feedback_type == "up":
            active_context.thumbs_up += 1
            active_context.save(update_fields=["thumbs_up"])
            return Response({"status": "success", "regenerate": False})
        elif feedback_type == "down":
            active_context.thumbs_down += 1
            threshold = getattr(settings, "READING_CONTEXT_REGENERATION_THRESHOLD", 5)
            regenerate = False
            if active_context.thumbs_down >= threshold:
                regenerate = True
                # Force regeneration via Celery task
                generate_reading_context_task.delay(reading.id, force_regeneration=True)
            active_context.save(update_fields=["thumbs_down"])
            return Response({"status": "success", "regenerate": regenerate})
        else:
            return Response(
                {"status": "error", "message": "Invalid feedback type"},
                status=status.HTTP_400_BAD_REQUEST,
            )
