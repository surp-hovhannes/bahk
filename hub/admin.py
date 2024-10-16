"""Admin forms."""
import datetime

from django.contrib import admin
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from hub.forms import CreateFastWithDatesAdminForm
from hub.models import Church, Day, Fast, Profile


def _concatenate_queryset(queryset, delim=", ", num=3):
    return delim.join([str(obj) for obj in queryset][:num])


@admin.register(Church, site=admin.site)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ("name", "get_fasts")
    ordering = ("name",)

    def get_fasts(self, church):
        return _concatenate_queryset(church.fasts.all())
    
    get_fasts.short_description = "Fasts"

@admin.register(Fast, site=admin.site)
class FastAdmin(admin.ModelAdmin):
    list_display = ("get_name", "church", "get_days", "culmination_feast", "culmination_feast_date", "description", "image",
                    "url_link",)
    list_display_links = ("url_link",)
    ordering = ("church", "name",)

    def get_name(self, fast):
        year = fast.days.first().date.year
        return f"{fast.name} ({year})"
    
    get_name.short_description = "Fast Name"
    
    def get_days(self, fast):
        return _concatenate_queryset(fast.days.all())
        
    get_days.short_description = "Days"

    def url_link(self, fast):
        if not fast.url:
            return ""
        return format_html('<a href="{}">{}</a>', fast.url, fast.url)
    
    url_link.short_descrption = "Link to Learn More"

    def get_urls(self):
        """Add endpoints to admin views."""
        return [
            path("create_fast_with_dates/", self.admin_site.admin_view(self.create_fast_with_dates),
                 name="create-fast-with-dates"),
        ] + super().get_urls()
    
    def create_fast_with_dates(self, request):
        """View to create fast along with its dates."""
        # form submitted with data
        if request.method == "POST":
            form = CreateFastWithDatesAdminForm(request.POST)
            if form.is_valid():
                fast = form.save()
                data = form.cleaned_data

                # days
                dates = [data["first_day"] + datetime.timedelta(days=num_days) 
                         for num_days in range(data["length_of_fast"])]
                days = [Day.objects.get_or_create(date=date)[0] for date in dates]
                fast.days.set(days)

                # go back to fast admin page
                obj_url = reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
                
                return redirect(to=obj_url)
        else:
            form = CreateFastWithDatesAdminForm()

        context = dict(
            self.admin_site.each_context(request),
            opts=Fast._meta,
            title="Create fast with dates",
            form=form
        )

        return TemplateResponse(request, "create_fast_with_dates.html", context)


@admin.register(Profile, site=admin.site)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "church", "get_fasts", "location", "receive_upcoming_fast_reminders", "profile_image", )
    ordering = ("church", "user",)

    def get_fasts(self, profile):
        return _concatenate_queryset(profile.fasts.all())
    
    get_fasts.short_description = "Fasts"


@admin.register(Day, site=admin.site)
class DayAdmin(admin.ModelAdmin):
    list_display = ("date", "church", "fast",)
    ordering = ("church", "date",)
