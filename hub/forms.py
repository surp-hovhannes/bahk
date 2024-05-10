"""Django form classes."""
import django.forms as forms

from hub.models import Church


class CreateFastWithDatesAdminForm(forms.Form):
    """Form to allow users to create fast with all its dates through admin interface."""
    name = forms.CharField(labels="Name of fast", max_length=128)
    church = forms.ChoiceField(labels="Church", choices=[])
    description = forms.CharField(label="Description of fast", widget=forms.Textarea())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["church"].choices = [(church.name, church.name) for church in Church.objects.all()]
