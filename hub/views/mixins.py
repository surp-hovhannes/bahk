from rest_framework.exceptions import ValidationError
from ..models import Church
from django.core.exceptions import ObjectDoesNotExist
import pytz
from pytz.exceptions import UnknownTimeZoneError
from hub.services.church_selection import content_church
from hub.utils import get_user_profile_safe


class ChurchContextMixin:
    """Mixin to provide church context based on the user's profile or query parameter."""

    def get_church(self):
        if self.request.user.is_authenticated:
            church = content_church(get_user_profile_safe(self.request.user))
        else:
            # If not authenticated, get church_id from query parameters
            church_id = self.request.query_params.get('church_id')
            if not church_id:
                raise ValidationError("church_id query parameter is required.")
            
            try:
                church = Church.objects.get(id=church_id)
            except Church.DoesNotExist:
                raise ValidationError("Church with the provided ID does not exist.")

        return church

class TimezoneMixin:
    """
    Mixin to handle timezone context for serializers.
    Expects 'tz' query parameter in IANA format (e.g., 'America/New_York').
    """
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['tz'] = self.get_timezone()
        return context

    def get_timezone(self):
        tz_str = self.request.query_params.get('tz')
        if not tz_str and self.request.user.is_authenticated:
            try:
                tz_str = self.request.user.profile.timezone
            except ObjectDoesNotExist:
                tz_str = None

        if tz_str:
            try:
                return pytz.timezone(tz_str)
            except UnknownTimeZoneError:
                pass
        return pytz.UTC
