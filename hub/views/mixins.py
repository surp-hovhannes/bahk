from rest_framework.exceptions import ValidationError
from ..models import Church


class ChurchContextMixin:
    """Mixin to provide church context based on the user's profile or query parameter."""

    def get_church(self):
        if self.request.user.is_authenticated:
            # Use the church associated with the user's profile
            church = self.request.user.profile.church
            if not church:
                raise ValidationError("No church associated with your profile.")
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
