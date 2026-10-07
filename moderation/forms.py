from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.urls import reverse

from moderation.context import shell_context


class ModerationLoginForm(AuthenticationForm):
    username = forms.EmailField(label="Account email", widget=forms.EmailInput(attrs={"autofocus": True}))


class ModerationLoginView(LoginView):
    template_name = "moderation/login.html"
    authentication_form = ModerationLoginForm
    next_page = "/moderation/"

    def get_context_data(self, **kwargs):
        return {
            **shell_context(self.request),
            **super().get_context_data(**kwargs),
            "title": "Moderation sign in",
            "app_path": reverse("moderation-login"),
        }
