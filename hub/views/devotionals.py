"""Views for accessing and editing daily devotionals."""
import datetime
import logging
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.utils.translation import activate, get_language_from_request

from .mixins import ChurchContextMixin, TimezoneMixin
from hub.models import Devotional, Fast
from hub.serializers import DevotionalSerializer


class LargeResultsSetPagination(PageNumberPagination):
    page_size = 45
    page_size_query_param = 'page_size'
    max_page_size = 100


class DevotionalByDateView(ChurchContextMixin, TimezoneMixin, generics.RetrieveAPIView):
    """
    API endpoint that provides details of a single devotional.

    If no devotional exists for the given date, returns null.

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

        # Try requested language, then fallback to default 'en'
        try:
            return Devotional.objects.get(day__church=church, day__date=target_date, language_code=lang)
        except Devotional.DoesNotExist:
            try:
                return Devotional.objects.get(day__church=church, day__date=target_date, language_code='en')
            except Devotional.DoesNotExist:
                pass
        try:
            return Devotional.objects.get(day__church=church, day__date=target_date)
        except Devotional.DoesNotExist:
            logging.error(f"Devotional not found for {target_date} for church {church.name}")
            return None


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
        - POST/PUT/PATCH/DELETE: Not supported
    """
    serializer_class = DevotionalSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Devotional.objects.all()
    
    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


class DevotionalListView(ChurchContextMixin, generics.ListAPIView):
    """
    API endpoint that provides a list of devotionals for a given church.

    Permissions:
        - GET: Any user can view devotional
        - POST/PUT/PATCH/DELETE: Not supported
    """
    serializer_class = DevotionalSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Devotional.objects.all()
    ORDERING_ALIASES = {
        "date": "day__date",
        "-date": "-day__date",
    }
    ALLOWED_ORDERING = {"day__date", "-day__date"}

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
        qs = Devotional.objects.select_related("day", "video").filter(
            day__church=church,
            language_code=lang,
        )
        if not qs.exists():
            qs = Devotional.objects.select_related("day", "video").filter(
                day__church=church,
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
