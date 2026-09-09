"""Read-only resource views for the public v1 API.

These views intentionally use shared models and the offline lectionary, but
never reuse the product API views: those routes cache, create calendar rows,
fetch passage text, or schedule background work.
"""

from django.db.models import Exists, F, Max, Min, OuterRef, Q
from django.http import Http404
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from bahk.public_api.v1.cache import cached_public_get
from bahk.public_api.v1.pagination import PublicApiPagination
from bahk.public_api.v1.serializers import (
    ChurchPublicSerializer,
    FastPublicSerializer,
    FeastPublicSerializer,
    IconPublicSerializer,
    ReadingPublicSerializer,
)
from bahk.public_api.v1.validation import PublicApiQuery, PublicApiView, resource_not_found
from hub.models import Church, Day, Fast, Feast, Reading
from icons.models import Icon


def annotated_fasts(queryset):
    """Add the only Fast date fields allowed by the public serializer."""
    return queryset.annotate(
        start_date=Min("days__date", filter=Q(days__church_id=F("church_id"))),
        end_date=Max("days__date", filter=Q(days__church_id=F("church_id"))),
    ).order_by("id")


class PublicApiResourceView(PublicApiView):
    """Shared anonymous JSON-only behavior for public resource views."""

    public_parameters = ()
    language_parameter = False
    church_parameter = False
    church_required = False
    date_required = False
    range_parameters = False

    @cached_public_get
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in ("GET", "HEAD"):
            return
        # Validate syntax before any database lookup or pagination count.
        query = self.public_query()
        if self.language_parameter:
            query.language()
        if self.church_parameter:
            query.church_id(required=self.church_required)
        if self.date_required:
            query.date("date", required=True)
        if self.range_parameters:
            query.effective_date_range()
        if isinstance(self, PublicApiListView):
            self.paginator.get_limit(request)
            self.paginator.get_offset(request)

    def public_query(self):
        if not hasattr(self, "_public_query"):
            self._public_query = PublicApiQuery(self.request.query_params)
        return self._public_query

    def serializer_context(self):
        query = self.public_query()
        return {"request": self.request, "lang": query.language() if self.language_parameter else None}

    def get_serializer_context(self):
        return self.serializer_context()


class PublicApiListView(PublicApiResourceView, ListAPIView):
    pagination_class = PublicApiPagination


class ChurchListView(PublicApiListView):
    serializer_class = ChurchPublicSerializer

    def get_queryset(self):
        return Church.objects.order_by("id")


class IconListView(PublicApiListView):
    church_parameter = True
    serializer_class = IconPublicSerializer
    public_parameters = ("church_id",)

    def get_queryset(self):
        query = self.public_query()
        church = query.church()
        queryset = Icon.objects.order_by("id")
        if church is not None:
            queryset = queryset.filter(church=church)
        return queryset


class FastListView(PublicApiListView):
    language_parameter = True
    church_parameter = True
    church_required = True
    range_parameters = True
    serializer_class = FastPublicSerializer
    public_parameters = ("church_id", "range")

    def get_queryset(self):
        query = self.public_query()
        church = query.church(required=True)
        start_date, end_date = query.effective_date_range()
        days_in_range = Day.objects.filter(church=church, fast=OuterRef("pk"), date__gte=start_date, date__lte=end_date)
        return annotated_fasts(Fast.objects.filter(church=church).filter(Exists(days_in_range)))


class FastDetailView(PublicApiResourceView, RetrieveAPIView):
    pagination_class = None
    language_parameter = True
    serializer_class = FastPublicSerializer

    def get_queryset(self):
        return annotated_fasts(Fast.objects.all())

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise resource_not_found("fast")


class FastByDateView(FastListView):
    public_parameters = ("church_id", "date")
    range_parameters = False
    date_required = True

    def get_queryset(self):
        query = self.public_query()
        church = query.church(required=True)
        target_date = query.date("date", required=True)
        matching_day = Day.objects.filter(church=church, fast=OuterRef("pk"), date=target_date)
        return annotated_fasts(Fast.objects.filter(church=church).filter(Exists(matching_day)))


class FastByFeastDateView(FastListView):
    public_parameters = ("church_id", "date")
    range_parameters = False
    date_required = True

    def get_queryset(self):
        query = self.public_query()
        church = query.church(required=True)
        target_date = query.date("date", required=True)
        return annotated_fasts(Fast.objects.filter(church=church, culmination_feast_date=target_date))


class ReadingByDateView(PublicApiResourceView):
    """Return citations already persisted for a church calendar day.

    Unlike the product endpoint, a public read never computes, persists, or
    fetches a missing day. An absent day is therefore a successful empty list.
    """

    public_parameters = ("church_id", "date")
    language_parameter = True
    church_parameter = True
    church_required = True
    date_required = True

    @cached_public_get
    def get(self, request, *args, **kwargs):
        query = self.public_query()
        church = query.church(required=True)
        target_date = query.date("date", required=True)
        readings = Reading.objects.filter(day__church=church, day__date=target_date).order_by("sequence", "id")
        return Response(
            {
                "date": target_date.isoformat(),
                "readings": ReadingPublicSerializer(readings, many=True, context=self.serializer_context()).data,
            }
        )


class FeastByDateView(PublicApiResourceView):
    """Resolve a date through the offline lectionary without creating a Feast."""

    public_parameters = ("church_id", "date")
    language_parameter = True
    church_parameter = True
    church_required = True
    date_required = True

    @cached_public_get
    def get(self, request, *args, **kwargs):
        query = self.public_query()
        church = query.church(required=True)
        target_date = query.date("date", required=True)

        from hub.services.feast_service import get_feast_for_date

        feast_data = get_feast_for_date(target_date, church) or []
        commemorations = [feast_data] if isinstance(feast_data, dict) else feast_data
        stable_ids = any(field.name == "observance_id" for field in Feast._meta.get_fields())
        field = "observance_id" if stable_ids else "name"
        keys = [
            item.get("observance_id") if stable_ids else item.get("name_en") or item.get("name")
            for item in commemorations
        ]
        keys = list(dict.fromkeys(key for key in keys if key))
        stored = (
            Feast.objects.filter(church=church, **{f"{field}__in": keys}).select_related("icon").order_by("id")
            if keys
            else []
        )
        by_key = {}
        for feast in stored:
            by_key.setdefault(getattr(feast, field), feast)
        feasts = [by_key[key] for key in keys if key in by_key]
        return Response(
            {
                "date": target_date.isoformat(),
                "feasts": FeastPublicSerializer(feasts, many=True, context=self.serializer_context()).data,
            }
        )
