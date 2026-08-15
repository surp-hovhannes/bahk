"""Views for returning data pertaining to feast days.

Feast names come from the offline ``armenian_lectionary`` engine (via
``hub.services.feast_service``); the previous sacredtradition.am scraping has been retired.
"""

import logging
from collections.abc import Mapping
from datetime import datetime

import sentry_sdk
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.utils.translation import activate, get_language_from_request
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.cache import feast_api_cache_key, invalidate_feast_api_cache_for_feast
from hub.models import Church, Feast, FeastContext
from hub.tasks import generate_feast_context_task
from hub.tasks.icon_tasks import match_icon_to_feast_task
from hub.tasks.llm_tasks import is_feast_context_generation_eligible
from hub.utils import get_user_profile_safe, get_or_create_feast_for_date
from icons.serializers import IconSerializer
from icons.models import Icon
from icons.views import IsAdminOrReadOnly


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
            church = profile.church if (profile and profile.church) else Church.objects.get(pk=Church.get_default_pk())
        else:
            church = Church.objects.get(pk=Church.get_default_pk())

        # Cache key for feast lookup (includes lang to prevent cross-language poisoning)
        cache_key = feast_api_cache_key(date_obj, church.id, lang)
        cached_result = cache.get(cache_key)
        if cached_result:
            return Response(cached_result)

        try:
            # Resolve the commemoration for this date and get the row holding its enrichment.
            # check_fast=False because the view should still return feasts even if a Fast exists.
            # There is no Day fallback any more: the engine names every date in its supported
            # range, so either it resolved a feast or there is genuinely none to show.
            feast, _, _ = get_or_create_feast_for_date(date_obj, church, check_fast=False)

            if feast is None:
                # No feast on this day
                response_data = {
                    "date": date_str,
                    "feast": None,
                }
                cache.set(cache_key, response_data, 3600)
                return Response(response_data)

            # Get translated feast name with proper fallback
            name_translated = getattr(feast, 'name_i18n', None)
            if not name_translated:
                # Fallback to base name field
                name_translated = feast.name
            
            # If name is still None or empty, treat as no feast
            if not name_translated or not name_translated.strip():
                response_data = {
                    "date": date_str,
                    "feast": None,
                }
                cache.set(cache_key, response_data, 3600)
                return Response(response_data)

            # Check if context exists and has all translations
            active_context = feast.active_context
            should_trigger_generation = is_feast_context_generation_eligible(feast)
            
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

            # Serialize icon if it exists
            icon_data = None
            if feast.icon:
                icon_serializer = IconSerializer(feast.icon, context={'request': request})
                icon_data = icon_serializer.data

            feast_data = {
                "id": feast.id,
                "name": name_translated,
                "designation": feast.designation,
                "icon": icon_data,
                **context_dict,
            }

            # Check if feast has a prayer for its designation
            feast_prayer_data = None
            if feast.designation:
                try:
                    from prayers.models import FeastPrayer
                    from prayers.serializers import FeastPrayerSerializer

                    feast_prayer = FeastPrayer.objects.get(designation=feast.designation)
                    serializer = FeastPrayerSerializer(
                        feast_prayer,
                        context={'request': request, 'lang': lang, 'feast': feast}
                    )
                    feast_prayer_data = serializer.data
                except FeastPrayer.DoesNotExist:
                    pass

            feast_data['prayer'] = feast_prayer_data

            response_data = {
                "date": date_str,
                "feast": feast_data,
            }

            # Cache successful response for 1 hour
            cache.set(cache_key, response_data, 3600)
            return Response(response_data)

        except Feast.DoesNotExist:
            # Feast may have been deleted between scheduling and execution — log and degrade gracefully
            logging.warning("Feast not found for date %s (church %s) — may have been deleted", date_obj, church)
            return Response(
                {"date": date_obj.isoformat(), "feast": None},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logging.error("Failed to get feast for date %s (church %s): %s", date_obj, church, e)
            # Return degraded response
            return Response(
                {
                    "date": date_obj.isoformat(),
                    "feast": None,
                    "error": "Feast data temporarily unavailable",
                },
                status=status.HTTP_200_OK  # Return 200 not 500 so clients handle gracefully
            )


class FeastMatchIconView(APIView):
    """Admin-only endpoint to enqueue icon matching for a feast missing an icon."""

    permission_classes = [IsAdminOrReadOnly]

    def post(self, request, feast_id: int):
        feast = get_object_or_404(Feast, pk=feast_id)

        if feast.icon_id is None:
            match_icon_to_feast_task.delay(feast.id)
            return Response(
                {
                    "status": "enqueued",
                    "feast_id": feast.id,
                    "reason": "icon missing",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "status": "skipped",
                "feast_id": feast.id,
                "reason": "icon already present",
            },
            status=status.HTTP_200_OK,
        )


class FeastAssignIconView(APIView):
    """Staff-only feast icon assignment preflight and mutation endpoint."""

    permission_classes = [IsAdminUser]

    @staticmethod
    def _icon_snapshot(icon):
        if icon is None:
            return None
        return {"id": icon.id, "title": icon.title}

    @classmethod
    def _feast_snapshot(cls, feast):
        return {
            "feast_id": feast.id,
            # No "date": a feast is a commemoration now, served on every day the engine names it,
            # so there is no single date this snapshot could report.
            "name": feast.name,
            "church_id": feast.church_id,
            "current_icon_id": feast.icon_id,
            "current_icon": cls._icon_snapshot(feast.icon),
        }

    @staticmethod
    def _is_positive_integer(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def get(self, request, feast_id: int):
        feast = get_object_or_404(
            Feast.objects.select_related("church", "icon"),
            pk=feast_id,
        )
        return Response(self._feast_snapshot(feast), status=status.HTTP_200_OK)

    def post(self, request, feast_id: int):
        if not isinstance(request.data, Mapping):
            return Response(
                {"non_field_errors": ["Request body must be a JSON object."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        icon_id = request.data.get("icon_id")
        replace = request.data.get("replace", False)

        errors = {}
        if not self._is_positive_integer(icon_id):
            errors["icon_id"] = "Must be a positive integer."
        if not isinstance(replace, bool):
            errors["replace"] = "Must be a boolean."
        if "expected_current_icon_id" not in request.data:
            errors["expected_current_icon_id"] = "This field is required."
        else:
            expected_icon_id = request.data["expected_current_icon_id"]
            if expected_icon_id is not None and not self._is_positive_integer(expected_icon_id):
                errors["expected_current_icon_id"] = "Must be a positive integer or null."
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        expected_icon_id = request.data["expected_current_icon_id"]
        with transaction.atomic():
            feast = get_object_or_404(
                Feast.objects.select_for_update().select_related("church"),
                pk=feast_id,
            )
            icon = get_object_or_404(Icon.objects.select_for_update(), pk=icon_id)

            if icon.church_id != feast.church_id:
                return Response(
                    {"icon_id": "Icon must belong to the feast's church."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if feast.icon_id != expected_icon_id:
                return Response(
                    {
                        "error": "The feast icon changed after preflight.",
                        "code": "stale_assignment",
                        "current_icon_id": feast.icon_id,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            previous_icon_id = feast.icon_id
            if previous_icon_id == icon.id:
                return Response(
                    {
                        "status": "unchanged",
                        "feast_id": feast.id,
                        "previous_icon_id": previous_icon_id,
                        "current_icon_id": icon.id,
                        "current_icon": self._icon_snapshot(icon),
                    },
                    status=status.HTTP_200_OK,
                )

            if previous_icon_id is not None and replace is not True:
                return Response(
                    {
                        "error": "The feast already has a different icon; set replace to true.",
                        "code": "replacement_required",
                        "current_icon_id": previous_icon_id,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            feast.icon = icon
            feast.save(update_fields=["icon"])
            assignment_status = "replaced" if previous_icon_id is not None else "assigned"
            return Response(
                {
                    "status": assignment_status,
                    "feast_id": feast.id,
                    "previous_icon_id": previous_icon_id,
                    "current_icon_id": icon.id,
                    "current_icon": self._icon_snapshot(icon),
                },
                status=status.HTTP_200_OK,
            )


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
            # Use atomic increment to prevent race conditions
            FeastContext.objects.filter(pk=active_context.pk).update(
                thumbs_up=F('thumbs_up') + 1
            )
            invalidate_feast_api_cache_for_feast(feast)
            return Response({"status": "success", "regenerate": False})
        elif feedback_type == "down":
            # Use atomic increment to prevent race conditions
            FeastContext.objects.filter(pk=active_context.pk).update(
                thumbs_down=F('thumbs_down') + 1
            )
            invalidate_feast_api_cache_for_feast(feast)
            # Refresh the object to get the updated value for threshold check
            active_context.refresh_from_db()
            threshold = getattr(settings, "FEAST_CONTEXT_REGENERATION_THRESHOLD", 5)
            regenerate = False
            if active_context.thumbs_down >= threshold:
                regenerate = True
                # Force regeneration via Celery task
                generate_feast_context_task.delay(feast.id, force_regeneration=True)
            return Response({"status": "success", "regenerate": regenerate})
        else:
            return Response(
                {"status": "error", "message": "Invalid feedback type"},
                status=status.HTTP_400_BAD_REQUEST,
            )
