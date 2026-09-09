"""Read-only resource views for the public v1 API.

These views intentionally use shared models and the offline lectionary, but
never reuse the product API views: those routes cache, create calendar rows,
fetch passage text, or schedule background work.
"""

from django.db.models import Exists, Max, Min, OuterRef
from django.http import Http404
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.renderers import JSONRenderer
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
        start_date=Min("days__date"),
        end_date=Max("days__date"),
    ).order_by("id")


class PublicApiResourceView(PublicApiView):
    """Shared anonymous JSON-only behavior for public resource views."""

    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]
    public_parameters = ()
    church_required = False

    @cached_public_get
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def public_query(self):
        if not hasattr(self, "_public_query"):
            self._public_query = PublicApiQuery(self.request.query_params)
        return self._public_query

    def serializer_context(self):
        query = self.public_query()
        return {"request": self.request, "lang": query.language()}

    def get_serializer_context(self):
        return self.serializer_context()


class PublicApiListView(PublicApiResourceView, ListAPIView):
    pagination_class = PublicApiPagination


class ChurchListView(PublicApiListView):
    serializer_class = ChurchPublicSerializer

    def get_queryset(self):
        return Church.objects.order_by("id")


class IconListView(PublicApiListView):
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
    serializer_class = FastPublicSerializer
    public_parameters = ("church_id", "range")
    church_required = True

    def get_queryset(self):
        query = self.public_query()
        church = query.church(required=True)
        start_date, end_date = query.effective_date_range()
        days_in_range = Day.objects.filter(fast=OuterRef("pk"), date__gte=start_date, date__lte=end_date)
        return annotated_fasts(Fast.objects.filter(church=church).filter(Exists(days_in_range)))


class FastDetailView(PublicApiResourceView, RetrieveAPIView):
    serializer_class = FastPublicSerializer

    def get_queryset(self):
        return annotated_fasts(Fast.objects.all())

    def get_object(self):
        try:
            return super().get_object()
        except Exception as exc:
            if isinstance(exc, Http404):
                raise resource_not_found("fast")
            raise


class FastByDateView(FastListView):
    public_parameters = ("church_id", "date")

    def get_queryset(self):
        query = self.public_query()
        church = query.church(required=True)
        target_date = query.date("date", required=True)
        matching_day = Day.objects.filter(fast=OuterRef("pk"), date=target_date)
        return annotated_fasts(Fast.objects.filter(church=church).filter(Exists(matching_day)))


class FastByFeastDateView(FastListView):
    public_parameters = ("church_id", "date")

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
    church_required = True

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
    church_required = True

    @cached_public_get
    def get(self, request, *args, **kwargs):
        query = self.public_query()
        church = query.church(required=True)
        target_date = query.date("date", required=True)

        from hub.services.feast_service import get_feast_for_date

        feast_data = get_feast_for_date(target_date, church) or {}
        name = feast_data.get("name_en") or feast_data.get("name")
        feast = None
        if name:
            feast = Feast.objects.filter(church=church, name=name).select_related("icon").first()
        return Response(
            {
                "date": target_date.isoformat(),
                "feast": FeastPublicSerializer(feast, context=self.serializer_context()).data
                if feast is not None
                else None,
            }
        )
