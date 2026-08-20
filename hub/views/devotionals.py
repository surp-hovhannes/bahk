"""Views for accessing and editing daily devotionals."""
import datetime
import logging
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.utils.translation import activate, get_language_from_request

from .mixins import ChurchContextMixin, TimezoneMixin
from hub.models import Devotional, Fast
from hub.serializers import DevotionalSerializer, DevotionalWriteSerializer
from icons.views import IsAdminOrReadOnly


DUPLICATE_DEVOTIONAL_ERROR = {
    "non_field_errors": [
        "A devotional with this day, order, and language code already exists."
    ]
}


def _matching_devotional_exists(serializer, *, exclude_id=None):
    """Return whether the validated write candidate conflicts with a stored row."""
    instance = serializer.instance
    day = serializer.validated_data.get("day", getattr(instance, "day", None))
    order = serializer.validated_data.get("order", getattr(instance, "order", None))
    language_code = serializer.validated_data.get(
        "language_code", getattr(instance, "language_code", None)
    )
    matches = Devotional.objects.filter(
        day=day,
        order=order,
        language_code=language_code,
    )
    if exclude_id is not None:
        matches = matches.exclude(pk=exclude_id)
    return matches.exists()


class LargeResultsSetPagination(PageNumberPagination):
    page_size = 45
    page_size_query_param = 'page_size'
    max_page_size = 100


class DevotionalByDateView(ChurchContextMixin, TimezoneMixin, generics.RetrieveAPIView):
    """
    API endpoint that provides details of a single devotional.

    If no devotional exists for the given date, returns HTTP 200 with a null body.
    Not every day has a devotional, so this is an expected, non-error response.

    Permissions:
        - GET: Any user can view devotional
        - POST/PUT/PATCH/DELETE: Not supported
    """
    serializer_class = DevotionalSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Devotional.objects.all()

    def get_object(self):
        church = self.get_church()
        # Activate requested language
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)

        date_str = self.request.query_params.get('date')
        tz = self.get_timezone()

        if date_str:
            try:
                # Parse the date string (expected format: yyyy-mm-dd)
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: yyyy-mm-dd.")
        else:
            # Default to the current date
            target_date = timezone.localdate(timezone=tz)

        # Try requested language, then fallback to 'en', then any language for that date.
        try:
            return Devotional.objects.get(day__church=church, day__date=target_date, language_code=lang)
        except Devotional.DoesNotExist:
            try:
                return Devotional.objects.get(day__church=church, day__date=target_date, language_code='en')
            except Devotional.DoesNotExist:
                pass
        devotional = Devotional.objects.filter(day__church=church, day__date=target_date).order_by('language_code').first()
        if devotional is None:
            # No devotional for this date — this is expected for some churches/days.
            logging.debug(f"No devotional for {target_date} for church {church.name}")
            return None
        return devotional

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return Response(None)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class DevotionalsByFastView(generics.ListAPIView):
    """
    API endpoint that provides a list of devotionals for a fast given its id.
    Results are paginated with a page size of 45 devotionals per page.

    URL Parameters:
        - fast_id: The ID of the fast for which to retrieve the devotionals.

    Permissions:
        - GET: Any user can view devotional
        - POST/PUT/PATCH/DELETE: Not supported
    """
    serializer_class = DevotionalSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = LargeResultsSetPagination
    
    def get_queryset(self):
        fast = Fast.objects.get(id=self.kwargs['fast_id'])
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        qs = Devotional.objects.filter(day__fast=fast, language_code=lang)
        if not qs.exists():
            qs = Devotional.objects.filter(day__fast=fast, language_code='en')
        return qs
        
    def get_paginated_response(self, data):
        """
        Override to ensure we return the expected pagination structure even if there's only one page.
        """
        return super().get_paginated_response(data)


class DevotionalDetailView(generics.RetrieveAPIView):
    """
    API endpoint that provides details of a single devotional.

    Permissions:
        - GET: Any user can view devotional
        - PATCH: Staff users only
        - POST/PUT/DELETE: Not supported
    """
    serializer_class = DevotionalSerializer
    permission_classes = [IsAdminOrReadOnly]
    queryset = Devotional.objects.all()
    
    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return DevotionalWriteSerializer
        return DevotionalSerializer

    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                instance = serializer.save()
        except IntegrityError:
            if _matching_devotional_exists(serializer, exclude_id=instance.pk):
                raise ValidationError(DUPLICATE_DEVOTIONAL_ERROR)
            raise
        return Response(
            DevotionalSerializer(instance, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )


class DevotionalListView(
    ChurchContextMixin,
    TimezoneMixin,
    generics.ListAPIView,
):
    """
    API endpoint that provides a list of devotionals for a given church.

    Permissions:
        - GET: Any user can view devotional
        - POST: Staff users only
        - PUT/PATCH/DELETE: Not supported
    """
    serializer_class = DevotionalSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = PageNumberPagination
    queryset = Devotional.objects.all()
    ORDERING_ALIASES = {
        "date": "day__date",
        "-date": "-day__date",
    }
    ALLOWED_ORDERING = {"day__date", "-day__date"}

    def get_serializer_class(self):
        if self.request.method == "POST":
            return DevotionalWriteSerializer
        return DevotionalSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                instance = serializer.save()
        except IntegrityError:
            if _matching_devotional_exists(serializer):
                raise ValidationError(DUPLICATE_DEVOTIONAL_ERROR)
            raise
        return Response(
            DevotionalSerializer(instance, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def _build_search_query(self, search_term, lang):
        """
        Build search query across devotional/video title/description fields with
        translation fallback support.
        """
        fields = [
            "description",
            "video__title",
            "video__description",
        ]

        modeltrans_languages = getattr(settings, "MODELTRANS_AVAILABLE_LANGUAGES", [])

        # Search requested language translations when available.
        if lang in modeltrans_languages and lang != "en":
            fields.extend([
                f"description_{lang}",
                f"video__title_{lang}",
                f"video__description_{lang}",
            ])

        # Always include English fallback translation fields.
        if "en" in modeltrans_languages:
            fields.extend([
                "description_en",
                "video__title_en",
                "video__description_en",
            ])

        query = Q()
        for field in fields:
            query |= Q(**{f"{field}__icontains": search_term})
        return query

    def _get_ordering(self):
        ordering = self.request.query_params.get("ordering")
        if not ordering:
            return None

        ordering = self.ORDERING_ALIASES.get(ordering, ordering)
        if ordering not in self.ALLOWED_ORDERING:
            return None
        return ordering

    def _get_limit(self):
        limit = self.request.query_params.get("limit")
        if not limit:
            return None
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return None
        return limit if limit > 0 else None

    def get_queryset(self):
        church = self.get_church()
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        local_today = timezone.localdate(timezone=self.get_timezone())
        qs = Devotional.objects.select_related("day", "video").filter(
            day__church=church,
            day__date__lte=local_today,
            language_code=lang,
        )
        if not qs.exists():
            qs = Devotional.objects.select_related("day", "video").filter(
                day__church=church,
                day__date__lte=local_today,
                language_code="en",
            )

        search_term = self.request.query_params.get("search")
        if search_term:
            qs = qs.filter(self._build_search_query(search_term, lang))

        ordering = self._get_ordering()
        if ordering:
            qs = qs.order_by(ordering, "order")

        return qs

    def list(self, request, *args, **kwargs):
        """
        Apply `limit` after DRF filter backends run to avoid returning a sliced
        queryset from `get_queryset()`, which can break backend filtering/ordering.
        """
        queryset = self.filter_queryset(self.get_queryset())

        limit = self._get_limit()
        if limit:
            queryset = queryset[:limit]

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
