"""Django form classes."""
import django.forms as forms


class CreateFastWithDatesAdminForm(forms.Form):
    """Form to allow users to create fast with all its dates through admin interface."""
    