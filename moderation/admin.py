from django.contrib import admin
from django.core.exceptions import PermissionDenied
from moderation.models import Responsibility


@admin.register(Responsibility)
class ResponsibilityAdmin(admin.ModelAdmin):
    list_display = ("user", "general", "crisis")
    autocomplete_fields = ("user",)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission

    def save_model(self, request, obj, form, change):
        if obj.user_id == request.user.pk:
            raise PermissionDenied("Another authorized administrator must assign your responsibilities.")
        super().save_model(request, obj, form, change)
