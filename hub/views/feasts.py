"""Views for returning data pertaining to feast days.

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

from hub.models import Church, Day, Feast
from hub.tasks import generate_feast_context_task
from hub.utils import get_user_profile_safe, scrape_feast


class GetFeastForDate(generics.GenericAPIView):
    """
    API view to provide feast information for a given date.

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
                "text": "AI-generated context text for the feast",
                "short_text": "Short 2-sentence summary",
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

        day, _ = Day.objects.get_or_create(date=date_obj, church=church)

        # If no feast exists for the requested day/church, scrape and persist it
        if not day.feasts.exists():
            feast_data = scrape_feast(date_obj, church)
            
            if feast_data:
                # Extract name fields
                name_en = feast_data.get("name_en", feast_data.get("name"))
                name_hy = feast_data.get("name_hy", None)
                
                if not name_en:
                    # If no English name, try to use name field directly
                    name_en = feast_data.get("name")
                
                if name_en:
                    # Get or create feast with English name
                    feast_obj, created = Feast.objects.get_or_create(
                        day=day,
                        defaults={"name": name_en}
                    )
                    
                    # Update translations if they are missing
                    if name_hy and not feast_obj.name_hy:
                        feast_obj.name_hy = name_hy
                        feast_obj.save(update_fields=['i18n'])

        # Now ensure we have up-to-date queryset
        day.refresh_from_db()

        # Get the feast for this day (if any)
        feast = day.feasts.first()
        
        if feast is None:
            # No feast on this day
            return Response({
                "date": date_str,
                "feast": None,
            })

        # Get translated feast name with proper fallback
        name_translated = getattr(feast, 'name_i18n', None)
        if not name_translated:
            # Fallback to base name field
            name_translated = feast.name
        
        # If name is still None or empty, treat as no feast
        if not name_translated or not name_translated.strip():
            return Response({
                "date": date_str,
                "feast": None,
            })

        # Check if context exists and has all translations
        active_context = feast.active_context
        should_trigger_generation = True
        
        # Don't trigger context generation if feast name includes "Fast"
        if "Fast" in feast.name:
            should_trigger_generation = False
        
        if active_context is None:
            # No context at all, trigger generation for all languages if appropriate
            if should_trigger_generation:
                logging.warning("No context found for feast %s", str(feast))
                logging.info("Enqueue context generation for feast %s (all languages)", feast.id)
                generate_feast_context_task.delay(feast.id)
            
            context_dict = {
                "text": "",
                "short_text": "",
                "context_thumbs_up": 0,
                "context_thumbs_down": 0,
            }
        else:
            # Get the requested language translations
            context_text = getattr(active_context, 'text_i18n', active_context.text)
            short_context_text = getattr(active_context, 'short_text_i18n', active_context.short_text)

            # Check if all languages have translations
            available_languages = getattr(settings, 'MODELTRANS_AVAILABLE_LANGUAGES', ['en', 'hy'])
            all_languages_present = True
            for available_lang in available_languages:
                if available_lang == 'en':
                    lang_text = active_context.text
                    lang_short = active_context.short_text
                else:
                    lang_text = getattr(active_context, f'text_{available_lang}', None)
                    lang_short = getattr(active_context, f'short_text_{available_lang}', None)
                
                if not lang_text or not lang_text.strip() or not lang_short or not lang_short.strip():
                    all_languages_present = False
                    break

            # If any translation is missing, trigger generation for all languages if appropriate
            if not all_languages_present and should_trigger_generation:
                logging.info(
                    "Context translations missing for feast %s, enqueuing generation for all languages",
                    feast.id
                )
                generate_feast_context_task.delay(feast.id)

            context_dict = {
                "text": context_text or "",
                "short_text": short_context_text or "",
                "context_thumbs_up": active_context.thumbs_up,
                "context_thumbs_down": active_context.thumbs_down,
            }

        feast_data = {
            "id": feast.id,
            "name": name_translated,
            **context_dict,
        }

        response_data = {
            "date": date_str,
            "feast": feast_data,
        }

        return Response(response_data)


class FeastContextFeedbackView(APIView):
    """
    API view to handle user feedback (thumbs up / thumbs down) for the AI-generated
    context text of a `Feast`.

    Permissions:
        - AllowAny

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
          regenerate the context.

    Responses:
        200 OK – JSON object `{ "status": "success", "regenerate": bool }`
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

