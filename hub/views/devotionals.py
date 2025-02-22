"""Views for accessing and editing daily devotionals."""
import datetime
import logging
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError

from .mixins import ChurchContextMixin, TimezoneMixin
from hub.models import Devotional
from hub.serializers import DevotionalSerializer


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
