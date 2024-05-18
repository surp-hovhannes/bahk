from django.contrib import admin

from hub.models import Church, Day, Fast, Profile


def _concatenate_queryset(queryset, delim=", "):
    return delim.join([str(obj) for obj in queryset])


@admin.register(Church, site=admin.site)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ("name",)
    ordering = ("name",)


@admin.register(Fast, site=admin.site)
class FastAdmin(admin.ModelAdmin):
    list_display = ("name", "church", "description", "image")
    ordering = ("church", "name",)


@admin.register(Profile, site=admin.site)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "church", "get_fasts",)
    ordering = ("church", "user",)

    def get_fasts(self, profile):
        return _concatenate_queryset(profile.fasts.all())
    
    get_fasts.short_description = "Fasts"


@admin.register(Day, site=admin.site)
class DayAdmin(admin.ModelAdmin):
    list_display = ("date", "get_fasts",)
    ordering = ("date",)

    def get_fasts(self, day):
        return _concatenate_queryset(day.fasts.all())
    
    get_fasts.short_description = "Fasts"
