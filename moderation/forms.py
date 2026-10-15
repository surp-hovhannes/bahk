from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.urls import reverse

from moderation.context import shell_context
from moderation.choices import action_label, correct_decisions, published_test_cases


class ReviewForm(forms.Form):
    action = forms.ChoiceField(
        label="Decision",
        error_messages={"required": "Choose a decision."},
        widget=forms.Select(attrs={"id": "review-action", "aria-describedby": "review-action-help"}),
    )
    note = forms.CharField(
        label="Notes",
        max_length=4000,
        error_messages={
            "required": "Add a note explaining your decision.",
            "max_length": "Keep notes under 4,000 characters.",
        },
        widget=forms.Textarea(
            attrs={"id": "review-note", "class": "vLargeTextField", "aria-describedby": "review-note-help"}
        ),
    )
    expected_outcome = forms.ChoiceField(
        label="Correct decision",
        required=False,
        error_messages={"invalid_choice": "Choose a correct decision from the list."},
        widget=forms.Select(attrs={"id": "review-expected", "aria-describedby": "review-expected-help"}),
    )
    regression_reference = forms.ChoiceField(
        label="Related test case (optional)",
        required=False,
        error_messages={"invalid_choice": "Choose an existing test case from the list."},
        widget=forms.Select(attrs={"id": "review-reference", "aria-describedby": "review-reference-help"}),
    )

    def __init__(self, *args, kind, actions, crisis=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].choices = [("", "Choose a decision…")] + [
            (value, action_label(value, crisis=crisis)) for value in actions
        ]
        self.fields["expected_outcome"].choices = [("", "Choose the correct decision…")] + list(correct_decisions(kind))
        self.test_cases = published_test_cases() if kind == "prayer" else ()
        self.fields["regression_reference"].choices = [("", "No related test case")] + list(self.test_cases)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "misclassification" and not cleaned.get("expected_outcome"):
            if "expected_outcome" not in self.errors:
                self.add_error("expected_outcome", "Choose the decision you think is correct.")
        return cleaned


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
