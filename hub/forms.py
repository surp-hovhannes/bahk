"""Django form classes."""
import datetime

from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django import forms
from django.contrib.auth.models import User
from s3_file_field.widgets import S3FileInput

from hub.constants import DATE_FORMAT_STRING
from hub.models import Church, Day, Devotional, Fast, Profile
from learning_resources.models import Video


_MAX_FAST_LENGTH = 60  # days


class AddDaysToFastAdminForm(forms.Form):
    dates = forms.CharField(
        help_text="Type in each date on a new line in the format month/day/year. "
                  "For example, 8/15/2024 for August 15, 2024",
        widget=forms.Textarea
    )

    def clean_dates(self):
        """Ensures that dates are entered properly (acceptable date format, line-separated)."""
        print(self.cleaned_data["dates"])
        dates = []
        for date_str in self.cleaned_data["dates"].splitlines():
            try:
                dates.append(datetime.datetime.strptime(date_str, DATE_FORMAT_STRING).date())
            except:
                raise ValidationError(f"{date_str} not in valid date format (<month>/<day>/<year>)")

        return dates


class CreateFastWithDatesAdminForm(forms.ModelForm):
    first_day = forms.DateField(widget=forms.SelectDateWidget)
    last_day = forms.DateField(widget=forms.SelectDateWidget)

    class Meta:
        model = Fast
        # "image" is excluded because it is not clear how to save an image with a form
        fields = ["name", "church", "year", "description", "culmination_feast",
                  "culmination_feast_date", "culmination_feast_salutation",
                  "culmination_feast_message", "culmination_feast_message_attribution", "url"]

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
        church_name = self.cleaned_data["church"].name
        days = Day.objects.filter(date__range=[first_day, last_day], church__name=church_name)
        if any(day.fast is not None for day in days):
            raise ValidationError(f"Fast overlaps with another fast from the {church_name} (not permitted).")


        return last_day


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    church = forms.ChoiceField(label="Churches", choices=[])

    class Meta:
        model = User
        fields = ["email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['church'].choices = [(church.name, church.name) for church in Church.objects.all()]

    def clean_email(self):
        new_email = self.cleaned_data.get("email")
        if User.objects.filter(email=new_email).exists():
            raise ValidationError(f"Cannot register with email {new_email}. User already exists with this address.")


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
        fields = ['church', 'profile_image','location', 'timezone', 'receive_upcoming_fast_reminders']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['church'].widget.attrs.update({'class': 'form-control'})
        self.fields['timezone'].widget.attrs.update({'class': 'form-control'})


class FastForm(forms.ModelForm):
    class Meta:
        model = Fast
        fields = ['name', 'description', 'image']


SUPPORTED_LANGUAGES = [
    # (code, display_name, required)
    ('en', 'English', True),
    ('hy', 'Armenian', False),
]

DEFAULT_LANGUAGE = 'en'


class CombinedDevotionalForm(forms.Form):
    """Single form to create videos and devotionals for selected languages."""

    # --- Date, Fast & Order (shared) ---
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text="Date for the devotional day. If a day already exists for this date, the fast will auto-fill.",
    )
    fast = forms.ModelChoiceField(
        queryset=Fast.objects.select_related('church').order_by('-year', 'name'),
        help_text="Auto-filled when an existing day matches the date above. Can be changed manually.",
    )
    order = forms.IntegerField(
        required=False,
        help_text="Only matters when multiple devotionals exist on the same day. "
        "Controls display order and must be unique per day and language.",
    )

    # --- Language selection ---
    languages = forms.MultipleChoiceField(
        choices=[(code, name) for code, name, _ in SUPPORTED_LANGUAGES],
        initial=[DEFAULT_LANGUAGE],
        help_text="English is always included. Select additional languages as needed.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for code, name, _ in SUPPORTED_LANGUAGES:
            self.fields[f'existing_video_{code}'] = forms.ModelChoiceField(
                queryset=Video.objects.order_by('-created_at'),
                required=False,
                label=f"Use existing {code.upper()} video",
                help_text="Select an existing video, or leave blank to create a new one.",
            )
            self.fields[f'video_title_{code}'] = forms.CharField(
                max_length=200, required=False, label=f"Video title ({code.upper()})",
            )
            self.fields[f'video_description_{code}'] = forms.CharField(
                widget=forms.Textarea(attrs={'rows': 3}),
                required=False,
                label=f"Video description ({code.upper()})",
            )
            self.fields[f'video_file_{code}'] = forms.CharField(
                required=False,
                widget=S3FileInput(attrs={'accept': 'video/*'}),
                label=f"Video file ({code.upper()})",
                help_text="Supported formats: MP4, WebM. Portrait orientation (9:16). Files up to 20MB.",
            )
            self.fields[f'video_thumbnail_{code}'] = forms.ImageField(
                required=False, label=f"Video thumbnail ({code.upper()})",
            )
            self.fields[f'devotional_description_{code}'] = forms.CharField(
                widget=forms.Textarea(attrs={'rows': 3}),
                required=False,
                label=f"Devotional description ({code.upper()})",
            )

    def get_language_fields(self):
        """Return structured field groups per language for template rendering."""
        selected = self.data.getlist('languages') if self.is_bound else [DEFAULT_LANGUAGE]
        result = []
        for code, name, required in SUPPORTED_LANGUAGES:
            result.append({
                'code': code,
                'name': name,
                'required': required,
                'selected': required or code in selected,
                'existing_video': self[f'existing_video_{code}'],
                'video_title': self[f'video_title_{code}'],
                'video_description': self[f'video_description_{code}'],
                'video_file': self[f'video_file_{code}'],
                'video_thumbnail': self[f'video_thumbnail_{code}'],
                'devotional_description': self[f'devotional_description_{code}'],
            })
        return result

    def clean_languages(self):
        langs = self.cleaned_data.get('languages', [])
        if DEFAULT_LANGUAGE not in langs:
            langs = [DEFAULT_LANGUAGE] + list(langs)
        return langs

    def _validate_video_side(self, cleaned, lang):
        """Validate one language's video fields. Require new-video fields if no existing video."""
        existing = cleaned.get(f'existing_video_{lang}')
        if not existing:
            if not cleaned.get(f'video_title_{lang}'):
                self.add_error(f'video_title_{lang}', 'Required when creating a new video.')
            if not cleaned.get(f'video_description_{lang}'):
                self.add_error(f'video_description_{lang}', 'Required when creating a new video.')
            if not cleaned.get(f'video_file_{lang}'):
                self.add_error(f'video_file_{lang}', 'Required when creating a new video.')

    def clean(self):
        cleaned = super().clean()
        selected_languages = cleaned.get('languages', [DEFAULT_LANGUAGE])

        for lang in selected_languages:
            self._validate_video_side(cleaned, lang)

        # Check unique constraints (day, order, language_code) for selected languages
        fast = cleaned.get('fast')
        date = cleaned.get('date')
        order = cleaned.get('order')
        if fast and date and order is not None:
            day = Day.objects.filter(fast=fast, date=date).first()
            if day:
                for lang in selected_languages:
                    if Devotional.objects.filter(
                        day=day, order=order, language_code=lang,
                    ).exists():
                        self.add_error(
                            'order',
                            f'A devotional with order={order} and language_code={lang} '
                            f'already exists for this day.',
                        )

        return cleaned
