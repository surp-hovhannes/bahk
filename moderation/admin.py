from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from moderation.models import Responsibility

SELF_ASSIGNMENT = "Another authorized administrator must assign your responsibilities."


class ResponsibilityForm(forms.ModelForm):
    acting_user = None

    class Meta:
        model = Responsibility
        fields = "__all__"

    def clean_user(self):
        user = self.cleaned_data["user"]
        if self.acting_user is not None and user.pk == self.acting_user.pk:
            # Shown on the form; save_model's refusal alone reached the page as a bare 403.
            raise forms.ValidationError(
                "You can't assign moderation responsibilities to your own account. "
                "Ask another superuser to add this row for you."
            )
        return user


@admin.register(Responsibility)
class ResponsibilityAdmin(admin.ModelAdmin):
    form = ResponsibilityForm
    list_display = ("user", "general", "crisis")
    autocomplete_fields = ("user",)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)  # a fresh class per request
        form.acting_user = request.user
        return form

    def save_model(self, request, obj, form, change):
        if obj.user_id == request.user.pk:
            raise PermissionDenied(SELF_ASSIGNMENT)
        super().save_model(request, obj, form, change)
