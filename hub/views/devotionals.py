"""Views for accessing and editing daily devotionals."""
import datetime
import logging
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

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
        return Devotional.objects.filter(day__fast=fast)
        
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

    def get_queryset(self):
        church = self.get_church()
        return Devotional.objects.filter(day__church=church)
