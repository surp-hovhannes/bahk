from django.contrib.auth.forms import UserCreationForm 
from django import forms
from django.contrib.auth.models import User

from hub.models import Church, Fast


CHURCHES = ((church.name, church.name) for church in Church.objects.all())
FASTS = ((fast.name, fast.name) for fast in Fast.objects.all())


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    church = forms.ChoiceField(label="Churches", choices=CHURCHES)
    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]


class JoinFastsForm(forms.Form):
    fasts = forms.ModelMultipleChoiceField(queryset=Fast.objects.all(), widget=forms.SelectMultiple)
