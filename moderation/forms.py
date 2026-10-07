from django import forms
from django.contrib.auth.forms import AuthenticationForm


class ModerationLoginForm(AuthenticationForm):
    username = forms.EmailField(label="Account email", widget=forms.EmailInput(attrs={"autofocus": True}))
