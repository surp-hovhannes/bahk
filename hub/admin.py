"""Admin forms."""
import datetime

from django.contrib import admin
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from hub.forms import AddDaysToFastAdminForm, CreateFastWithDatesAdminForm
from hub.models import Church, Day, Fast, Profile, Reading


_MAX_NUM_TO_SHOW = 3  # maximum object names to show in list


def _concatenate_queryset(queryset, delim=", ", num=_MAX_NUM_TO_SHOW):
    needs_ellipsis = len(queryset) > num
    return delim.join([str(obj) for obj in queryset][:num]) + (needs_ellipsis * ",...")


def _get_fk_links_url(fk_queryset, fk_name, max_num_to_show=_MAX_NUM_TO_SHOW):
    num_to_display = min(fk_queryset.count(), max_num_to_show)

    args = []
    is_partial_list = False
    for i, fk in enumerate(fk_queryset):
        if i == num_to_display:
            is_partial_list = True  # not all fasts will be shown
            break
        url = reverse(f"admin:hub_{fk_name}_change", args=[fk.pk])
        args += [url, fk]
    
    format_string = ", ".join(num_to_display * ['<a href="{}">{}</a>'])
    # add ellipsis if only partial list of fasts shown
    format_string += ",..." if is_partial_list else ""

    return format_html(format_string, *args)


@admin.register(Church, site=admin.site)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ("name", "fast_links",)
    ordering = ("name",)
    list_display_links = ("name", "fast_links",)

    def fast_links(self, church):
        return _get_fk_links_url(church.fasts.all(), "fast")
    
    fast_links.short_description = "Fasts"


@admin.register(Fast, site=admin.site)
class FastAdmin(admin.ModelAdmin):
    list_display = ("get_name", "church_link", "get_days", "culmination_feast", "culmination_feast_date", "description", "image",
                    "url_link",)
    list_display_links = ("get_name", "church_link", "url_link",)
    ordering = ("-year", "church", "name",)

    def get_name(self, fast):
        return str(fast)
    
    get_name.short_description = "Fast Name"

    def church_link(self, fast):
        if not fast.church:
            return ""
        url = reverse("admin:hub_church_change", args=[fast.church.pk])
        return format_html('<a href="{}">{}</a>', url, fast.church.name)
    
    church_link.short_description = "Church"
    
    def get_days(self, fast):
        return _concatenate_queryset(fast.days.all())
        
    get_days.short_description = "Days"

    def url_link(self, fast):
        if not fast.url:
            return ""
        return format_html('<a href="{}">{}</a>', fast.url, fast.url)
    
    url_link.short_description = "Link to Learn More"

    def get_urls(self):
        """Add endpoints to admin views."""
        return [
            path("create_fast_with_dates/", self.admin_site.admin_view(self.create_fast_with_dates),
                 name="create-fast-with-dates"),
            path(
                "<int:pk>/change/duplicate_fast_with_new_dates/", 
                self.admin_site.admin_view(self.duplicate_fast_with_new_dates),
                name="duplicate-fast-with-new-dates",
            ),
            path(
                "<int:pk>/change/add_days_to_fast/",
                self.admin_site.admin_view(self.add_days_to_fast),
                name="add-days-to-fast",
            ),
        ] + super().get_urls()
    
    def add_days_to_fast(self, request, pk):
        """View to add days to a fast."""
        fast = Fast.objects.get(pk=pk)
        # form submitted with data
        if request.method == "POST":
            form = AddDaysToFastAdminForm(request.POST)
            if form.is_valid():
                days = [Day.objects.get_or_create(date=date)[0] for date in form.cleaned_data["dates"]]
                fast.days.add(*days)

                obj_url = reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
                
                return redirect(to=obj_url)
        else:
            form = AddDaysToFastAdminForm()

        fast_name = Fast.objects.get(pk=pk).name 
        context = dict(
            self.admin_site.each_context(request),
            opts=Fast._meta,
            title=f"Add days to {fast_name}",
            form=form,
            fast_name=fast_name,
        )

        return TemplateResponse(request, "add_days_to_fast.html", context)

    def create_fast_with_dates(self, request):
        """View to create fast along with its dates."""
        # form submitted with data
        if request.method == "POST":
            form = CreateFastWithDatesAdminForm(request.POST)
            if form.is_valid():
                fast = form.save()
                data = form.cleaned_data

                # create days for fast
                dates = [data["first_day"] + datetime.timedelta(days=num_days) 
                         for num_days in range(data["length_of_fast"])]
                for date in dates:
                    Day.objects.create(date=date, fast=fast, church=data["church"])

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

    def duplicate_fast_with_new_dates(self, request, pk):
        """View to duplicate fast with new dates for a new year. Previous participants are not added."""
        old_fast = Fast.objects.get(pk=pk)
        # form submitted with data
        if request.method == "POST":
            form = CreateFastWithDatesAdminForm(request.POST)
            if form.is_valid():
                duplicate_fast = form.save()
                data = form.cleaned_data

                # use previous fast's image since not set in form
                duplicate_fast.image = old_fast.image
                duplicate_fast.image_thumbnail = old_fast.image_thumbnail

                # days
                dates = [data["first_day"] + datetime.timedelta(days=num_days) 
                        for num_days in range(data["length_of_fast"])]
                days = [Day.objects.get_or_create(date=date)[0] for date in dates]
                duplicate_fast.days.set(days)
                duplicate_fast.save()  # run save method to ensure year is set

                # go back to fast admin page
                obj_url = reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
                
                return redirect(to=obj_url)
        else:
            form = CreateFastWithDatesAdminForm(initial={
                "name": old_fast.name,
                "church": old_fast.church,
                "culmination_feast": old_fast.culmination_feast,
                "description": old_fast.description,
                "url": old_fast.url,
            })

        fast_name = Fast.objects.get(pk=pk).name 
        context = dict(
            self.admin_site.each_context(request),
            opts=Fast._meta,
            title=f"Duplicate {fast_name} with new dates",
            form=form,
            fast_name=fast_name,
        )

        return TemplateResponse(request, "duplicate_fast_with_new_dates.html", context)


@admin.register(Profile, site=admin.site)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "church_link", "fast_links", "location", "receive_upcoming_fast_reminders", "profile_image", )
    list_display_links = ("user", "church_link", "fast_links",)
    ordering = ("church", "user",)

    def church_link(self, fast):
        if not fast.church:
            return ""
        url = reverse("admin:hub_church_change", args=[fast.church.pk])
        return format_html('<a href="{}">{}</a>', url, fast.church.name)
    
    church_link.short_description = "Church"

    def fast_links(self, profile):
        return _get_fk_links_url(profile.fasts.all(), "fast")
    
    fast_links.short_description = "Fasts"


@admin.register(Day, site=admin.site)
class DayAdmin(admin.ModelAdmin):
    list_display = ("date", "church_link", "fast_link", "reading_links")
    ordering = ("church", "date",)

    def church_link(self, day):
        if not day.fast or not day.fast.church:
            return ""
        url = reverse("admin:hub_church_change", args=[day.fast.church.pk])
        return format_html('<a href="{}">{}</a>', url, day.fast.church.name)

    church_link.short_description = "Church"

    def fast_link(self, day):
        if not day.fast:
            return ""
        url = reverse("admin:hub_fast_change", args=[day.fast.pk])
        return format_html('<a href="{}">{}</a>', url, day.fast.name)
    
    fast_link.short_description = "Fast"

    def reading_links(self, day):
        return _get_fk_links_url(day.readings.all(), "reading")
    
    reading_links.short_description = "Readings"


class ReadingYearFilter(admin.SimpleListFilter):
    title = "year"
    parameter_name = "year"
    
    def lookups(self, request, model_admin):
        all_years = set(r.day.date.year for r in model_admin.model.objects.all())
        return [(y, y) for y in all_years]

    def queryset(self, request, queryset):
        if self.value() is not None:
            year = int(self.value())
            return queryset.filter(
                day__date__gte=datetime.date(year, 1, 1),
                day__date__lte=datetime.date(year, 12, 31)
            )
    

@admin.register(Reading, site=admin.site)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("church_link", "day", "__str__", "book", "start_chapter", "start_verse",)
    list_display_links = ("church_link", "day", "__str__",)
    list_filter = (ReadingYearFilter, "book", "start_chapter", "start_verse",)
    ordering = ("day", "book", "start_chapter", "start_verse",)
    
    def church_link(self, reading):
        if not reading.day and not reading.day.church:
            return ""
        url = reverse("admin:hub_church_change", args=[reading.day.church.pk])
        return format_html('<a href="{}">{}</a>', url, reading.day.church.name)
    
    church_link.short_description = "Church"
