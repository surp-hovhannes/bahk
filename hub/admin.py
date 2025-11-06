"""Admin forms."""

import datetime

from django.contrib import admin
from django.db import models
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.text import Truncator
from django.contrib import messages
from markdownx.admin import MarkdownxModelAdmin
import logging

from hub.forms import AddDaysToFastAdminForm, CreateFastWithDatesAdminForm
from hub.models import (
    Church,
    Day,
    Devotional,
    DevotionalSet,
    Fast,
    Feast,
    FeastContext,
    LLMPrompt,
    PatristicQuote,
    Profile,
    Reading,
    ReadingContext,
)
from hub.tasks import generate_reading_context_task

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
    list_display = (
        "name",
        "fast_links",
    )
    ordering = ("name",)
    list_display_links = (
        "name",
        "fast_links",
    )

    def fast_links(self, church):
        return _get_fk_links_url(church.fasts.all(), "fast")

    fast_links.short_description = "Fasts"


@admin.register(Devotional, site=admin.site)
class DevotionalAdmin(admin.ModelAdmin):
    list_display = ("title", "fast", "date", "order")
    list_filter = ("day__fast", "day__date")
    ordering = ("day__date",)
    search_fields = ("video__title", "description")
    raw_id_fields = ("video", "day")

    def title(self, obj):
        return obj.video.title if obj.video else ""

    title.admin_order_field = "video__title"

    def fast(self, obj):
        return obj.day.fast if obj.day else ""

    fast.admin_order_field = "day__fast__name"

    def date(self, obj):
        return obj.day.date if obj.day else ""

    date.admin_order_field = "day__date"


@admin.register(DevotionalSet, site=admin.site)
class DevotionalSetAdmin(admin.ModelAdmin):
    list_display = ('title', 'fast', 'number_of_days', 'image_preview', 'created_at')
    search_fields = ('title', 'description', 'fast__name')
    list_filter = ('fast', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview', 'number_of_days')
    raw_id_fields = ('fast',)
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'fast')
        }),
        ('Media', {
            'fields': ('image', 'image_preview')
        }),
        ('Statistics', {
            'fields': ('number_of_days',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def image_preview(self, obj):
        if obj.image:
            try:
                # Try to get cached URL first
                if obj.cached_thumbnail_url:
                    return format_html(
                        '<img src="{}" style="max-height: 50px; max-width: 100px;"/>',
                        obj.cached_thumbnail_url
                    )
                # Fall back to direct thumbnail URL
                return format_html(
                    '<img src="{}" style="max-height: 50px; max-width: 100px;"/>',
                    obj.thumbnail.url
                )
            except (AttributeError, ValueError, OSError) as e:
                logging.error(f"Thumbnail error for DevotionalSet {obj.id}: {e}")
                return "Thumbnail error"
        return "No image"

    image_preview.short_description = 'Image Preview'

    def number_of_days(self, obj):
        return obj.number_of_days
    number_of_days.short_description = 'Number of Days'


@admin.register(Fast, site=admin.site)
class FastAdmin(admin.ModelAdmin):
    list_display = (
        "get_name",
        "church_link",
        "get_days",
        "culmination_feast_date",
        "get_description",
        "image_link",
        "participant_count",
    )
    list_display_links = ["get_name"]
    ordering = ("-year", "church", "name")
    list_filter = ("church", "year")
    sortable_by = ("get_name", "participant_count")
    exclude = ("name", "description", "culmination_feast")  # Avoid duplicate with translation fields

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(participant_count=models.Count("profiles"))
        return queryset

    def get_name(self, fast):
        return fast

    get_name.short_description = "Fast Name"
    get_name.admin_order_field = "name"

    def church_link(self, fast):
        if not fast.church:
            return ""
        url = reverse("admin:hub_church_change", args=[fast.church.pk])
        return format_html('<a href="{}">{}</a>', url, fast.church.name)

    church_link.short_description = "Church"

    def get_days(self, fast):
        return _concatenate_queryset(fast.days.all(), ", ", 5)

    get_days.short_description = "Days"

    def get_description(self, fast):
        if not fast.description:
            return ""
        t = Truncator(fast.description)
        return t.chars(25)

    get_description.short_description = "Description"

    def image_link(self, fast):
        if not fast.image:
            return ""
        url = fast.image.url
        return format_html('<a href="{}">{}</a>', url, "Image Link")

    def participant_count(self, fast):
        return fast.participant_count

    participant_count.short_description = "Participants"
    participant_count.admin_order_field = "participant_count"

    def get_urls(self):
        """Add endpoints to admin views."""
        return [
            path(
                "create_fast_with_dates/",
                self.admin_site.admin_view(self.create_fast_with_dates),
                name="create-fast-with-dates",
            ),
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
                days = [
                    Day.objects.get_or_create(date=date)[0]
                    for date in form.cleaned_data["dates"]
                ]
                fast.days.add(*days)

                obj_url = reverse(
                    f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"
                )

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
                dates = [
                    data["first_day"] + datetime.timedelta(days=num_days)
                    for num_days in range(data["length_of_fast"])
                ]
                for date in dates:
                    Day.objects.create(date=date, fast=fast, church=data["church"])

                # go back to fast admin page
                obj_url = reverse(
                    f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"
                )

                return redirect(to=obj_url)
        else:
            form = CreateFastWithDatesAdminForm()

        context = dict(
            self.admin_site.each_context(request),
            opts=Fast._meta,
            title="Create fast with dates",
            form=form,
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
                dates = [
                    data["first_day"] + datetime.timedelta(days=num_days)
                    for num_days in range(data["length_of_fast"])
                ]
                days = [Day.objects.get_or_create(date=date)[0] for date in dates]
                duplicate_fast.days.set(days)
                duplicate_fast.save()  # run save method to ensure year is set

                # go back to fast admin page
                obj_url = reverse(
                    f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"
                )

                return redirect(to=obj_url)
        else:
            form = CreateFastWithDatesAdminForm(
                initial={
                    "name": old_fast.name,
                    "church": old_fast.church,
                    "culmination_feast": old_fast.culmination_feast,
                    "description": old_fast.description,
                    "url": old_fast.url,
                }
            )

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
    list_display = (
        "user",
        "church_link",
        "fast_links",
        "name",
        "location",
        "profile_image_link",
        "joined_date",
    )
    list_display_links = ("user", "church_link", "fast_links", "profile_image_link")
    ordering = ("church", "user")
    list_filter = ("church", "fasts", "user__date_joined")
    sortable_by = ("user", "joined_date")

    def church_link(self, fast):
        if not fast.church:
            return ""
        url = reverse("admin:hub_church_change", args=[fast.church.pk])
        return format_html('<a href="{}">{}</a>', url, fast.church.name)

    church_link.short_description = "Church"

    def fast_links(self, profile):
        return _get_fk_links_url(profile.fasts.all(), "fast")

    fast_links.short_description = "Fasts"

    def joined_date(self, profile):
        return profile.user.date_joined

    joined_date.short_description = "Joined"
    joined_date.admin_order_field = "user__date_joined"

    def profile_image_link(self, profile):
        if not profile.profile_image:
            return ""
        url = profile.profile_image.url
        return format_html('<a href="{}">Image Link</a>', url)

    profile_image_link.short_description = "Profile Image"


@admin.register(Day, site=admin.site)
class DayAdmin(admin.ModelAdmin):
    list_display = ("date", "church_link", "fast_link", "reading_links")
    ordering = (
        "church",
        "date",
    )
    list_filter = ("church", "fast")

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
                day__date__lte=datetime.date(year, 12, 31),
            )


@admin.register(Reading, site=admin.site)
class ReadingAdmin(admin.ModelAdmin):
    list_display = (
        "church_link",
        "day",
        "__str__",
        "book",
        "start_chapter",
        "start_verse",
    )
    list_display_links = (
        "church_link",
        "day",
        "__str__",
    )
    list_filter = (
        ReadingYearFilter,
        "book",
        "start_chapter",
        "start_verse",
    )
    ordering = (
        "day",
        "book",
        "start_chapter",
        "start_verse",
    )
    actions = ["force_regenerate_context", "compare_prompts"]
    exclude = ("book",)  # Avoid duplicate with translation fields

    fieldsets = (
        (None, {
            'fields': ('day', 'start_chapter', 'start_verse', 'end_chapter', 'end_verse')
        }),
        ('Translations', {
            'fields': ('book_en', 'book_hy')
        }),
    )

    def compare_prompts(self, request, queryset):
        """Redirect to a page to compare different LLM prompts for selected readings."""
        if queryset.count() != 1:
            self.message_user(
                request,
                "Please select exactly one reading to compare prompts.",
                level=messages.ERROR
            )
            return
        
        reading = queryset.first()
        return redirect(reverse('hub_reading_compare_prompts', args=[reading.id]))

    compare_prompts.short_description = "Compare LLM prompts for selected reading"

    def force_regenerate_context(self, request, queryset):
        """Force enqueues context regeneration for selected readings."""
        count = queryset.count()
        for reading in queryset:
            generate_reading_context_task.delay(reading.id, force_regeneration=True)
        self.message_user(
            request, f"Initiated forced regeneration for {count} readings."
        )

    force_regenerate_context.short_description = (
        "Force regenerate AI context for selected readings"
    )

    def church_link(self, reading):
        if not reading.day or not reading.day.church:
            return ""
        url = reverse("admin:hub_church_change", args=[reading.day.church.pk])
        return format_html('<a href="{}">{}</a>', url, reading.day.church.name)

    church_link.short_description = "Church"


@admin.register(LLMPrompt, site=admin.site)
class LLMPromptAdmin(admin.ModelAdmin):
    list_display = ("id", "model", "applies_to", "active", "context_count", "role", "prompt_preview")
    list_filter = ("model", "applies_to", "active")
    search_fields = ("role", "prompt")
    ordering = ("id", "active")
    actions = ["duplicate_prompt", "make_active"]

    def context_count(self, obj):
        # Manually count contexts based on applies_to
        if obj.applies_to == 'readings':
            return obj.readingcontext_set.count()
        elif obj.applies_to == 'feasts':
            return obj.feastcontext_set.count()
        return 0
    context_count.short_description = "Contexts"
    context_count.admin_order_field = 'context_count'

    def prompt_preview(self, obj):
        return Truncator(obj.prompt).chars(100)

    prompt_preview.short_description = "Prompt Preview"

    def duplicate_prompt(self, request, queryset):
        """Create a copy of selected prompt and open it for editing."""
        if queryset.count() != 1:
            self.message_user(
                request,
                "Please select exactly one prompt to duplicate.",
                level=messages.ERROR
            )
            return

        prompt = queryset.first()
        # Create a new prompt with the same data
        new_prompt = LLMPrompt.objects.create(
            model=prompt.model,
            applies_to=prompt.applies_to,
            role=prompt.role,
            prompt=prompt.prompt,
            active=False  # Set as inactive by default
        )
        # Redirect to the change form for the new prompt
        self.message_user(
            request,
            f"Successfully duplicated prompt '{prompt.role}' for model '{prompt.model}'.",
        )
        return redirect(reverse('admin:hub_llmprompt_change', args=[new_prompt.id]))
    
    duplicate_prompt.short_description = "Duplicate selected prompt"

    def make_active(self, request, queryset):
        """Make the selected prompt active and deactivate others with same model and role."""
        if queryset.count() != 1:
            self.message_user(
                request,
                "Please select exactly one prompt to make active.",
                level=messages.ERROR
            )
            return

        prompt = queryset.first()
        
        # First, deactivate the current active prompt
        current_active = LLMPrompt.objects.filter(
            active=True
        ).first()
        
        if current_active:
            current_active.active = False
            current_active.save()
            self.message_user(
                request,
                f"Deactivated previous active prompt '{current_active.role}' for model '{current_active.model}'.",
            )
        
        # Then activate the selected prompt
        prompt.active = True
        prompt.save()
        
        self.message_user(
            request,
            f"Successfully made prompt '{prompt.role}' for model '{prompt.model}' active.",
        )
    
    make_active.short_description = "Make selected prompt active"


@admin.register(ReadingContext, site=admin.site)
class ReadingContextAdmin(admin.ModelAdmin):
    list_display = (
        "reading",
        "prompt",
        "active",
        "thumbs_up",
        "thumbs_down",
        "time_of_generation",
        "text_preview",
    )
    list_display_links = (
        "reading",
        "prompt",
    )
    list_filter = ("active", "prompt__model", "reading__day__date")
    search_fields = ("text", "reading__book")
    ordering = ("-time_of_generation",)
    raw_id_fields = ("reading", "prompt")
    readonly_fields = ("time_of_generation",)
    exclude = ("text",)  # Avoid duplicate with translation fields

    fieldsets = (
        (None, {
            'fields': ('reading', 'prompt', 'active', 'thumbs_up', 'thumbs_down', 'time_of_generation')
        }),
        ('Context Translations', {
            'fields': ('text_en', 'text_hy')
        }),
    )

    def text_preview(self, obj):
        return Truncator(obj.text).chars(100)

    text_preview.short_description = "Text Preview"


@admin.register(Feast, site=admin.site)
class FeastAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "date",
        "church",
    )
    list_display_links = (
        "name",
        "date",
    )
    list_filter = (
        "date",
        "church",
    )
    search_fields = ("name",)
    ordering = ("-date",)
    exclude = ("name",)  # Avoid duplicate with translation fields

    fieldsets = (
        (None, {
            'fields': ('date', 'church')
        }),
        ('Feast Name Translations', {
            'fields': ('name_en', 'name_hy')
        }),
    )


@admin.register(FeastContext, site=admin.site)
class FeastContextAdmin(admin.ModelAdmin):
    list_display = (
        "feast",
        "prompt",
        "active",
        "thumbs_up",
        "thumbs_down",
        "time_of_generation",
        "text_preview",
    )
    list_display_links = (
        "feast",
        "prompt",
    )
    list_filter = ("active", "prompt__model", "feast__date")
    search_fields = ("text", "feast__name")
    ordering = ("-time_of_generation",)
    raw_id_fields = ("feast", "prompt")
    readonly_fields = ("time_of_generation",)
    exclude = ("text", "short_text")  # Avoid duplicate with translation fields

    fieldsets = (
        (None, {
            'fields': ('feast', 'prompt', 'active', 'thumbs_up', 'thumbs_down', 'time_of_generation')
        }),
        ('Context Translations', {
            'fields': ('text_en', 'text_hy', 'short_text_en', 'short_text_hy')
        }),
    )

    def text_preview(self, obj):
        return Truncator(obj.text).chars(100)

    text_preview.short_description = "Text Preview"


@admin.register(PatristicQuote, site=admin.site)
class PatristicQuoteAdmin(MarkdownxModelAdmin):
    """Admin interface for PatristicQuote model."""
    
    list_display = (
        'text_preview',
        'attribution',
        'church_links',
        'fast_links',
        'tag_list',
        'created_at'
    )
    list_filter = ('churches', 'fasts', 'tags', 'created_at', 'updated_at')
    search_fields = ('text', 'attribution')
    raw_id_fields = ('churches', 'fasts')
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('churches', 'fasts')
    exclude = ('text', 'attribution')  # Avoid duplicate with translation fields
    
    fieldsets = (
        (None, {
            'fields': ('text_en', 'text_hy', 'attribution_en', 'attribution_hy')
        }),
        ('Organization', {
            'fields': ('churches', 'fasts', 'tags')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def text_preview(self, obj):
        """Display first 100 characters of the quote."""
        return Truncator(obj.text).chars(100)
    
    text_preview.short_description = 'Quote'
    
    def church_links(self, quote):
        """Display links to associated churches."""
        return _get_fk_links_url(quote.churches.all(), 'church')
    
    church_links.short_description = 'Churches'
    
    def fast_links(self, quote):
        """Display links to associated fasts."""
        if quote.fasts.exists():
            return _get_fk_links_url(quote.fasts.all(), 'fast')
        return '-'
    
    fast_links.short_description = 'Fasts'
    
    def tag_list(self, obj):
        """Display tags as comma-separated list."""
        return ', '.join(tag.name for tag in obj.tags.all())
    
    tag_list.short_description = 'Tags'
