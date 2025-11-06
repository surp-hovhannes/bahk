"""Views for returning data pertaining to feasts.
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

from hub.models import Church, Feast
from hub.tasks import generate_feast_context_task
from hub.utils import get_user_profile_safe, scrape_feast


class GetFeastByDate(generics.GenericAPIView):
    """
    API view to provide feast information for a specific date.

    Permissions:
        - AllowAny: No authentication required

    Query Parameters:
        - date (str): Optional. The date to get the feast for in the format YYYY-MM-DD.

    Returns:
        - A JSON response with the following structure:
        {
            "date": "YYYY-MM-DD",
            "feast": {
                "id": 1,
                "name": "Feast Name",
                "context": "AI-generated context text for the feast",
                "context_short_text": "Short version of context",
                "context_thumbs_up": 10,
                "context_thumbs_down": 2
            }
        }

    Example Response:
        {
            "date": "2024-12-25",
            "feast": {
                "id": 1,
                "name": "Christmas",
                "context": "AI-generated context text for the feast",
                "context_short_text": "Short version of context",
                "context_thumbs_up": 10,
                "context_thumbs_down": 2
            }
        }
    """

    queryset = Feast.objects.all()

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

        # Try to get existing feast
        feast = Feast.objects.filter(date=date_obj, church=church).first()

        # If no feast exists, scrape and create it
        if not feast:
            feast_data = scrape_feast(date_obj, church)
            if feast_data:
                # Extract and remove all name-related fields to handle them separately
                name_en = feast_data.pop("name_en", feast_data.get("name"))
                name_hy = feast_data.pop("name_hy", None)
                feast_data.pop("name", None)

                # Create the feast
                feast, created = Feast.objects.get_or_create(
                    date=date_obj,
                    church=church,
                    defaults={"name": name_en}
                )

                # Update translations if they are missing
                if name_en and feast.name != name_en:
                    feast.name = name_en
                    feast.save(update_fields=['name'])
                
                if name_hy and not feast.name_hy:
                    feast.name_hy = name_hy
                    feast.save(update_fields=['i18n'])

        # If still no feast, return empty response
        if not feast:
            response_data = {
                "date": date_str,
                "feast": None,
            }
            return Response(response_data)

        # Get translated feast name
        feast_name_translated = getattr(feast, 'name_i18n', feast.name)

        # Check if context exists and has all translations
        active_context = feast.active_context
        if active_context is None:
            # No context at all, trigger generation for all languages
            logging.warning("No context found for feast %s", str(feast))
            logging.info("Enqueue context generation for feast %s (all languages)", feast.id)
            generate_feast_context_task.delay(feast.id)
            context_dict = {
                "context": "",
                "context_short_text": "",
                "context_thumbs_up": 0,
                "context_thumbs_down": 0,
            }
        else:
            # Get the requested language translation
            context_text = getattr(active_context, 'text_i18n', active_context.text)
            short_text = getattr(active_context, 'short_text_i18n', active_context.short_text or "")

            # Check if all languages have translations
            available_languages = getattr(settings, 'MODELTRANS_AVAILABLE_LANGUAGES', ['en', 'hy'])
            all_languages_present = True
            for available_lang in available_languages:
                if available_lang == 'en':
                    lang_text = active_context.text
                    lang_short_text = active_context.short_text
                else:
                    lang_text = getattr(active_context, f'text_{available_lang}', None)
                    lang_short_text = getattr(active_context, f'short_text_{available_lang}', None)
                
                if not lang_text or not lang_text.strip():
                    all_languages_present = False
                    break

            # If any translation is missing, trigger generation for all languages
            if not all_languages_present:
                logging.info(
                    "Context translations missing for feast %s, enqueuing generation for all languages",
                    feast.id
                )
                generate_feast_context_task.delay(feast.id)

            context_dict = {
                "context": context_text or "",
                "context_short_text": short_text or "",
                "context_thumbs_up": active_context.thumbs_up,
                "context_thumbs_down": active_context.thumbs_down,
            }

        response_data = {
            "date": date_str,
            "feast": {
                "id": feast.id,
                "name": feast_name_translated,
                **context_dict,
            },
        }

        return Response(response_data)


class FeastContextFeedbackView(APIView):
    """
    API view to handle user feedback (thumbs up / thumbs down) for the AI-generated
    context text of a `Feast`.

    Permissions:
        - AllowAny (adjust as needed, e.g. `IsAuthenticated` if you only want
          logged-in users to submit feedback)

    Path Parameters (URL):
        pk (int): Primary key of the `Feast` object.

    Request Body (JSON):
        {
            "feedback_type": "up"   # valid values: "up" or "down"
        }

    Behaviour:
        • If `feedback_type` == "up" – increments `context_thumbs_up`.
        • If `feedback_type` == "down" – increments `context_thumbs_down`.
        • When downs reach the configurable threshold
          (`settings.FEAST_CONTEXT_REGENERATION_THRESHOLD`, default **5**)
          a new Celery task (`generate_feast_context_task`) is enqueued to
          regenerate the context.  Vote counters remain stored so front-end can
          still display them until the new context is generated (the task will
          reset counts on success).

    Responses:
        200 OK – JSON object `{ "status": "success", "regenerate": bool }`
                  where `regenerate` is *true* only when regeneration is
                  triggered (on a down-vote).
        400 Bad Request – when an invalid `feedback_type` is supplied.
        404 Not Found – when the supplied `pk` does not correspond to a Feast.
    """

    def post(self, request, pk):
        feast = get_object_or_404(Feast, pk=pk)
        active_context = feast.active_context
        
        # Check if active context exists
        if active_context is None:
            # Trigger context generation if not already in progress
            generate_feast_context_task.delay(feast.id)
            return Response(
                {
                    "status": "error",
                    "message": "No context available for this feast. Context generation has been queued."
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
            threshold = getattr(settings, "FEAST_CONTEXT_REGENERATION_THRESHOLD", 5)
            regenerate = False
            if active_context.thumbs_down >= threshold:
                regenerate = True
                # Force regeneration via Celery task
                generate_feast_context_task.delay(feast.id, force_regeneration=True)
            active_context.save(update_fields=["thumbs_down"])
            return Response({"status": "success", "regenerate": regenerate})
        else:
            return Response(
                {"status": "error", "message": "Invalid feedback type"},
                status=status.HTTP_400_BAD_REQUEST,
            )
