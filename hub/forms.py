"""Django form classes."""
from django.contrib.auth.forms import UserCreationForm 
from django.core.exceptions import ValidationError
from django import forms
from django.contrib.auth.models import User

from hub.models import Church, Day, Fast, Profile


_MAX_FAST_LENGTH = 60  # days


class CreateFastWithDatesAdminForm(forms.ModelForm):
    first_day = forms.DateField(widget=forms.SelectDateWidget)
    last_day = forms.DateField(widget=forms.SelectDateWidget)

    class Meta:
        model = Fast
        # "image" is excluded because it is not clear how to save an image with a form
        fields = ["name", "church", "description", "culmination_feast", "culmination_feast_date", "url"]

    def clean_last_day(self):
        """Ensure last day is 
        - not before the first day
        - not longer than max length
        - before the culmination feast.
        """
        # last day must be after first day
        first_day = self.cleaned_data["first_day"]
        last_day = self.cleaned_data["last_day"]
        length_of_fast = (last_day - first_day).days + 1
        if length_of_fast < 1:
            raise ValidationError(f"Last day ({last_day}) cannot be before the first day ({first_day})")
        # length of fast must be within limit 
        if length_of_fast > _MAX_FAST_LENGTH:
            raise ValidationError(f"Tried to create a fast lasting {length_of_fast} days. "
                                  f"Fasts longer than {_MAX_FAST_LENGTH} days are not permitted.")
        self.cleaned_data["length_of_fast"] = length_of_fast
        # last day must be before culmination feast, if present
        culmination_feast_date = self.cleaned_data.get("culmination_feast_date")
        if culmination_feast_date is not None and culmination_feast_date <= last_day:
            raise ValidationError(
                f"Last day ({last_day}) must be before culmination feast ({culmination_feast_date})"
            )

        # also checks that does not overlap with other fasts from the same church
        days = Day.objects.filter(date__range=[first_day, last_day])
        church_name = self.cleaned_data["church"].name
        names_of_churches_with_overlapping_fasts = sum([[f.church.name for f in day.fasts.all()] for day in days], [])
        if church_name in names_of_churches_with_overlapping_fasts:
            raise ValidationError("Fast overlaps with another fast from the same church (not permitted).")


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    church = forms.ChoiceField(label="Churches", choices=[])
    
    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['church'].choices = [(church.name, church.name) for church in Church.objects.all()]


class JoinFastsForm(forms.Form):
    fasts = forms.ModelMultipleChoiceField(queryset=Fast.objects.none(), widget=forms.SelectMultiple)
    
    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)  # Extract the request object
        super().__init__(*args, **kwargs)
        if request is not None:
            self.fields["fasts"].queryset = Fast.objects.filter(church=request.user.profile.church)


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['church', 'profile_image','location', 'receive_upcoming_fast_reminders']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['church'].widget.attrs.update({'class': 'form-control'})


class FastForm(forms.ModelForm):
    class Meta:
        model = Fast
        fields = ['name', 'description', 'image'] 
