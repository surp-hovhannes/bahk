"""Admin forms."""
import datetime

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from hub.forms import CreateFastWithDatesAdminForm
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
    list_display = ("user", "church", "get_fasts", "profile_image", 'receive_upcoming_fast_reminders', )
    ordering = ("church", "user",)

    def get_fasts(self, profile):
        return _concatenate_queryset(profile.fasts.all())
    
    get_fasts.short_description = "Fasts"


class DayAdminForm(forms.ModelForm):
    class Meta:
        model = Day
        fields = ["date", "fasts"]

    def clean_fasts(self):
        fasts = self.cleaned_data["fasts"]
        church_names = [fast.church.name for fast in fasts]
        if len(church_names) > len(set(church_names)):
            date = str(self.cleaned_data["date"])
            print(date)
            raise ValidationError("Only one fast per church on a given date is permitted.", code="invalid")
        
        return fasts


@admin.register(Day, site=admin.site)
class DayAdmin(admin.ModelAdmin):
    list_display = ("date", "get_fasts",)
    ordering = ("date",)
    form = DayAdminForm

    def get_fasts(self, day):
        return _concatenate_queryset(day.fasts.all())
    
    get_fasts.short_description = "Fasts"
