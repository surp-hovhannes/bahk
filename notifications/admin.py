from django.contrib import admin
from .models import DeviceToken

# Register your models here.

@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ('token', 'created_at')
    search_fields = ('token',)
    ordering = ('-created_at',)
