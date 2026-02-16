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
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from markdownx.admin import MarkdownxModelAdmin
import logging

from django.db import transaction

from hub.forms import (
    AddDaysToFastAdminForm,
    CombinedDevotionalForm,
    CreateFastWithDatesAdminForm,
    SUPPORTED_LANGUAGES,
)
from learning_resources.models import Video
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
from hub.services.bible_api_service import BibleAPIService, fetch_text_for_reading
from hub.tasks import (
    generate_reading_context_task,
    generate_feast_context_task,
    match_icon_to_feast_task,
)

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

    def get_urls(self):
        return [
            path(
                "create_combined/",
                self.admin_site.admin_view(self.create_combined_devotional),
                name="create-combined-devotional",
            ),
            path(
                "lookup_fast_by_date/",
                self.admin_site.admin_view(self.lookup_fast_by_date),
                name="lookup-fast-by-date",
            ),
        ] + super().get_urls()

    def lookup_fast_by_date(self, request):
        """AJAX endpoint: given a date, return the fast(s) associated with existing Days."""
        date_str = request.GET.get("date", "")
        if not date_str:
            return JsonResponse({"fasts": []})
        days = Day.objects.filter(date=date_str, fast__isnull=False).select_related("fast")
        fasts = [{"id": day.fast.id, "name": str(day.fast)} for day in days]
        # Deduplicate (multiple Days on the same date with different churches but same fast)
        seen = set()
        unique_fasts = []
        for f in fasts:
            if f["id"] not in seen:
                seen.add(f["id"])
                unique_fasts.append(f)
        return JsonResponse({"fasts": unique_fasts})

    def _get_or_create_video(self, data, lang):
        """Return an existing or newly created Video for the given language."""
        video = data.get(f'existing_video_{lang}')
        if video:
            if video.category != 'devotional':
                video.category = 'devotional'
                video.save(update_fields=['category'])
            return video

        video = Video(
            title=data[f'video_title_{lang}'],
            description=data[f'video_description_{lang}'],
            video=data[f'video_file_{lang}'],
            category='devotional',
            language_code=lang,
        )
        if data.get(f'video_thumbnail_{lang}'):
            video.thumbnail = data[f'video_thumbnail_{lang}']
        video.save()
        return video

    def create_combined_devotional(self, request):
        """View to create videos and devotionals for selected languages."""
        if request.method == "POST":
            form = CombinedDevotionalForm(request.POST, request.FILES)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    with transaction.atomic():
                        # 1. Get or create Day
                        day, _ = Day.objects.get_or_create(
                            date=data['date'],
                            fast=data['fast'],
                            defaults={'church': data['fast'].church},
                        )

                        # 2. Create video + devotional for each selected language
                        selected_languages = data['languages']
                        first_devotional = None
                        for lang in selected_languages:
                            video = self._get_or_create_video(data, lang)
                            devotional = Devotional.objects.create(
                                day=day,
                                video=video,
                                description=data.get(f'devotional_description_{lang}') or '',
                                order=data.get('order'),
                                language_code=lang,
                            )
                            if first_devotional is None:
                                first_devotional = devotional

                    lang_names = ', '.join(
                        name for code, name, _ in SUPPORTED_LANGUAGES
                        if code in selected_languages
                    )
                    messages.success(
                        request,
                        f"Devotionals created successfully for: {lang_names}.",
                    )
                    return redirect(
                        reverse(
                            f"admin:{self.opts.app_label}_{self.opts.model_name}_change",
                            args=[first_devotional.pk],
                        )
                    )
                except Exception as e:
                    messages.error(request, f"Error creating devotionals: {e}")
        else:
            form = CombinedDevotionalForm()

        context = dict(
            self.admin_site.each_context(request),
            opts=Devotional._meta,
            title="Create combined devotional",
            form=form,
            language_fields=form.get_language_fields(),
        )
        return TemplateResponse(
            request,
            "admin/hub/devotional/create_combined.html",
            context,
        )


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

                # Copy translated fields (django-modeltrans stores in _en, _hy suffixes)
                # This ensures all language versions are preserved
                duplicate_fast.name_en = old_fast.name_en
                duplicate_fast.name_hy = old_fast.name_hy
                duplicate_fast.description_en = old_fast.description_en
                duplicate_fast.description_hy = old_fast.description_hy
                duplicate_fast.culmination_feast_en = old_fast.culmination_feast_en
                duplicate_fast.culmination_feast_hy = old_fast.culmination_feast_hy
                duplicate_fast.culmination_feast_salutation_en = old_fast.culmination_feast_salutation_en
                duplicate_fast.culmination_feast_salutation_hy = old_fast.culmination_feast_salutation_hy
                duplicate_fast.culmination_feast_message_en = old_fast.culmination_feast_message_en
                duplicate_fast.culmination_feast_message_hy = old_fast.culmination_feast_message_hy
                duplicate_fast.culmination_feast_message_attribution_en = old_fast.culmination_feast_message_attribution_en
                duplicate_fast.culmination_feast_message_attribution_hy = old_fast.culmination_feast_message_attribution_hy

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
                    "culmination_feast_salutation": old_fast.culmination_feast_salutation,
                    "culmination_feast_message": old_fast.culmination_feast_message,
                    "culmination_feast_message_attribution": old_fast.culmination_feast_message_attribution,
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
        "text_version",
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
    actions = ["force_regenerate_context", "compare_prompts", "fetch_bible_text"]
    readonly_fields = ("text_fetched_at", "has_fums_token", "fetch_text_link")
    exclude = ("book", "text", "fums_token")  # Avoid duplicates with translation fields

    fieldsets = (
        (None, {
            'fields': ('day', 'start_chapter', 'start_verse', 'end_chapter', 'end_verse')
        }),
        ('Translations', {
            'fields': ('book_en', 'book_hy', 'text_en', 'text_hy')
        }),
        ('Bible Text (API.Bible)', {
            'fields': ('text_version', 'text_copyright', 'text_fetched_at', 'has_fums_token', 'fetch_text_link'),
            'classes': ('collapse',),
            'description': (
                'FUMS (Fair Use Management System) tokens are required by API.Bible\'s terms of use. '
                'Each token is sent to the FUMS endpoint when a user views scripture, allowing '
                'API.Bible to track anonymized usage for rights holders and publishers.'
            ),
        }),
    )

    def has_fums_token(self, obj):
        return bool(obj.fums_token)

    has_fums_token.short_description = "FUMS token captured"
    has_fums_token.boolean = True

    def get_urls(self):
        """Add per-reading endpoint to fetch Bible text."""
        custom_urls = [
            path(
                "<int:pk>/fetch_bible_text/",
                self.admin_site.admin_view(self.fetch_bible_text_view),
                name="hub_reading_fetch_bible_text",
            ),
        ]
        return custom_urls + super().get_urls()

    def fetch_text_link(self, obj):
        """Render a button to fetch/re-fetch Bible text for this reading."""
        if not obj or not obj.pk:
            return "-"
        url = reverse("admin:hub_reading_fetch_bible_text", args=[obj.pk])
        label = "Re-fetch Bible text" if obj.text else "Fetch Bible text"
        return format_html('<a class="button" href="{}">{}</a>', url, label)

    fetch_text_link.short_description = "Fetch from API.Bible"

    def fetch_bible_text_view(self, request, pk: int):
        """Admin view to fetch Bible text for a single reading."""
        try:
            reading = Reading.objects.get(pk=pk)
        except Reading.DoesNotExist as exc:
            raise Http404 from exc
        if not self.has_change_permission(request, obj=reading):
            raise PermissionDenied

        try:
            service = BibleAPIService()
        except ValueError:
            self.message_user(
                request,
                "BIBLE_API_KEY is not configured. Cannot fetch text.",
                level=messages.ERROR,
            )
            return redirect(reverse("admin:hub_reading_change", args=[pk]))

        success = fetch_text_for_reading(reading, service=service)
        if success:
            self.message_user(
                request,
                f"Successfully fetched Bible text for {reading}.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f"Failed to fetch Bible text for {reading}. Check logs for details.",
                level=messages.ERROR,
            )
        return redirect(reverse("admin:hub_reading_change", args=[pk]))

    def fetch_bible_text(self, request, queryset):
        """Fetch Bible text from API.Bible for selected readings."""
        try:
            service = BibleAPIService()
        except ValueError:
            self.message_user(
                request,
                "BIBLE_API_KEY is not configured. Cannot fetch text.",
                level=messages.ERROR,
            )
            return

        success_count = 0
        fail_count = 0
        for reading in queryset:
            if fetch_text_for_reading(reading, service=service):
                success_count += 1
            else:
                fail_count += 1

        parts = [f"Fetched text for {success_count} reading(s)."]
        if fail_count:
            parts.append(f"{fail_count} failed (check logs).")
        self.message_user(
            request,
            " ".join(parts),
            level=messages.SUCCESS if fail_count == 0 else messages.WARNING,
        )

    fetch_bible_text.short_description = "Fetch Bible text from API.Bible"

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

    fieldsets = (
        (None, {
            'fields': ('model', 'applies_to', 'role', 'prompt', 'active')
        }),
        ('Important Notes', {
            'description': (
                '<strong>For Prayer Requests:</strong> The prompt must include <code>{title}</code> and '
                '<code>{description}</code> placeholders where the prayer request content will be inserted. '
                'The prompt should instruct the LLM to return a JSON response with these fields: '
                '<code>"approved"</code>, <code>"reason"</code>, <code>"concerns"</code>, <code>"severity"</code>, '
                '<code>"requires_human_review"</code>, and <code>"suggested_action"</code>.<br><br>'
                '<strong style="color: #c00;">Important:</strong> If your prompt includes a JSON example, '
                'you must escape the curly braces by doubling them: use <code>{{</code> and <code>}}</code> '
                'instead of <code>{</code> and <code>}</code>. Otherwise, Python\'s string formatting will '
                'interpret them as placeholders and cause errors.<br><br>'
                '<strong>For Feasts:</strong> The prompt must instruct the LLM to return a JSON response '
                'with two fields: <code>"text"</code> (detailed explanation) and <code>"short_text"</code> '
                '(2-sentence summary). Example instruction: "Return your response as JSON with two fields: '
                '\\"short_text\\": A 2-sentence summary, and \\"text\\": A detailed explanation."<br><br>'
                '<strong>For Readings:</strong> The prompt should return plain text context for the Bible passage.'
            ),
            'fields': ()
        }),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            context_count=models.Count('readingcontext', distinct=True)
        )

    def context_count(self, obj):
        return obj.context_count
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
            role=prompt.role,
            prompt=prompt.prompt,
            applies_to=prompt.applies_to,
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

        # First, deactivate the current active prompt for this applies_to type
        current_active = LLMPrompt.objects.filter(
            active=True,
            applies_to=prompt.applies_to
        ).first()

        if current_active:
            current_active.active = False
            current_active.save()
            self.message_user(
                request,
                f"Deactivated previous active prompt '{current_active.role}' for {current_active.applies_to}.",
            )

        # Then activate the selected prompt
        prompt.active = True
        prompt.save()

        self.message_user(
            request,
            f"Successfully made prompt '{prompt.role}' for {prompt.applies_to} active.",
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


class FeastYearFilter(admin.SimpleListFilter):
    """Custom filter to filter feasts by year."""

    title = "Year"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        """Return years that have feasts."""
        years = Feast.objects.dates("day__date", "year", order="DESC")
        return [(year.year, year.year) for year in years]

    def queryset(self, request, queryset):
        """Filter queryset by year."""
        if self.value():
            return queryset.filter(
                day__date__year=self.value()
            )


@admin.register(Feast, site=admin.site)
class FeastAdmin(admin.ModelAdmin):
    list_display = (
        "church_link",
        "day",
        "__str__",
        "name",
    )
    list_display_links = (
        "church_link",
        "day",
        "__str__",
    )
    list_filter = (
        FeastYearFilter,
        "day__church",
        "designation",
    )
    search_fields = ("name", "name_en", "name_hy")
    ordering = ("day",)
    raw_id_fields = ("day", "icon")
    actions = [
        "force_rematch_icon",
        "match_icon_if_missing",
        "force_regenerate_context",
        "regenerate_context_with_instructions",
    ]
    exclude = ("name",)  # Avoid duplicate with translation fields
    readonly_fields = ("icon_rematch_links",)

    fieldsets = (
        (None, {
            'fields': ('day',)
        }),
        ('Classification', {
            'fields': ('designation',)
        }),
        ('Icon', {
            'fields': ('icon', 'icon_rematch_links')
        }),
        ('Translations', {
            'fields': ('name_en', 'name_hy')
        }),
    )

    def get_urls(self):
        """Add per-feast endpoints to trigger icon matching."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:pk>/rematch_icon_force/",
                self.admin_site.admin_view(self.rematch_icon_force_view),
                name="hub_feast_rematch_icon_force",
            ),
            path(
                "<int:pk>/rematch_icon_if_missing/",
                self.admin_site.admin_view(self.rematch_icon_if_missing_view),
                name="hub_feast_rematch_icon_if_missing",
            ),
            path(
                "regenerate_context_instructions/",
                self.admin_site.admin_view(self.regenerate_context_with_instructions_view),
                name="hub_feast_regenerate_context_instructions",
            ),
        ]
        return custom_urls + urls

    def icon_rematch_links(self, feast):
        """Render links to trigger icon matching for this feast in admin."""
        if not feast or not feast.pk:
            return "-"

        url_force = reverse("admin:hub_feast_rematch_icon_force", args=[feast.pk])
        url_if_missing = reverse(
            "admin:hub_feast_rematch_icon_if_missing", args=[feast.pk]
        )
        return format_html(
            '<a class="button" href="{}">Force re-match icon</a>&nbsp;'
            '<a class="button" href="{}">Match icon only if missing</a>',
            url_force,
            url_if_missing,
        )

    icon_rematch_links.short_description = "Icon matching"

    def _enqueue_icon_match_for_feast(self, request, feast, *, force: bool) -> bool:
        """
        Enqueue icon matching for a feast.

        Returns True if a Celery task was enqueued, False otherwise.
        """
        if force:
            if feast.icon_id is not None:
                feast.icon = None
                feast.save(update_fields=["icon"])
            match_icon_to_feast_task.delay(feast.id)
            return True

        # Only if missing
        if feast.icon_id is None:
            match_icon_to_feast_task.delay(feast.id)
            return True

        return False

    def rematch_icon_force_view(self, request, pk: int):
        try:
            feast = Feast.objects.select_related("day", "day__church").get(pk=pk)
        except Feast.DoesNotExist as exc:
            raise Http404 from exc
        if not self.has_change_permission(request, obj=feast):
            raise PermissionDenied

        self._enqueue_icon_match_for_feast(request, feast, force=True)
        self.message_user(
            request,
            f"Enqueued icon re-match for feast {feast.id}.",
            level=messages.SUCCESS,
        )
        return redirect(reverse("admin:hub_feast_change", args=[feast.pk]))

    def rematch_icon_if_missing_view(self, request, pk: int):
        try:
            feast = Feast.objects.select_related("day", "day__church").get(pk=pk)
        except Feast.DoesNotExist as exc:
            raise Http404 from exc
        if not self.has_change_permission(request, obj=feast):
            raise PermissionDenied

        enqueued = self._enqueue_icon_match_for_feast(request, feast, force=False)
        if enqueued:
            self.message_user(
                request,
                f"Enqueued icon match for feast {feast.id}.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f"Feast {feast.id} already has an icon; nothing to do.",
                level=messages.WARNING,
            )
        return redirect(reverse("admin:hub_feast_change", args=[feast.pk]))

    def force_rematch_icon(self, request, queryset):
        """Clear current icon and enqueue icon matching for selected feasts."""
        enqueued = 0
        for feast in queryset:
            if self._enqueue_icon_match_for_feast(request, feast, force=True):
                enqueued += 1
        self.message_user(
            request,
            f"Enqueued forced icon re-match for {enqueued} feasts.",
            level=messages.SUCCESS,
        )

    force_rematch_icon.short_description = "Force re-match icon for selected feasts"

    def match_icon_if_missing(self, request, queryset):
        """Enqueue icon matching only for selected feasts missing an icon."""
        enqueued = 0
        skipped = 0
        for feast in queryset:
            if self._enqueue_icon_match_for_feast(request, feast, force=False):
                enqueued += 1
            else:
                skipped += 1
        self.message_user(
            request,
            f"Enqueued icon matching for {enqueued} feasts; skipped {skipped} (already had icon).",
            level=messages.SUCCESS,
        )

    match_icon_if_missing.short_description = (
        "Match icon only if missing for selected feasts"
    )

    def force_regenerate_context(self, request, queryset):
        """Force enqueues context regeneration for selected feasts."""
        count = queryset.count()
        for feast in queryset:
            generate_feast_context_task.delay(feast.id, force_regeneration=True)
        self.message_user(
            request, f"Initiated forced regeneration for {count} feasts."
        )

    force_regenerate_context.short_description = (
        "Force regenerate AI context for selected feasts"
    )

    def regenerate_context_with_instructions(self, request, queryset):
        """Redirect to intermediate page for providing improvement instructions."""
        selected = queryset.values_list('pk', flat=True)
        return redirect(
            f"{reverse('admin:hub_feast_regenerate_context_instructions')}?ids={','.join(str(pk) for pk in selected)}"
        )

    regenerate_context_with_instructions.short_description = (
        "Regenerate context with improvement instructions"
    )

    def regenerate_context_with_instructions_view(self, request):
        """View to handle regeneration with improvement instructions."""
        if not self.has_change_permission(request):
            raise PermissionDenied

        ids_param = request.GET.get('ids', '') or request.POST.get('ids', '')
        if not ids_param:
            self.message_user(
                request,
                "No feasts selected.",
                level=messages.ERROR
            )
            return redirect(reverse('admin:hub_feast_changelist'))

        feast_ids = [int(pk) for pk in ids_param.split(',') if pk.strip()]
        feasts = Feast.objects.filter(pk__in=feast_ids).select_related('day')

        if not feasts.exists():
            self.message_user(
                request,
                "No valid feasts found.",
                level=messages.ERROR
            )
            return redirect(reverse('admin:hub_feast_changelist'))

        if request.method == 'POST':
            improvement_instructions = request.POST.get('improvement_instructions', '').strip()
            if not improvement_instructions:
                self.message_user(
                    request,
                    "Please provide improvement instructions.",
                    level=messages.ERROR
                )
            else:
                count = feasts.count()
                for feast in feasts:
                    generate_feast_context_task.delay(
                        feast.id,
                        force_regeneration=True,
                        improvement_instructions=improvement_instructions
                    )
                self.message_user(
                    request,
                    f"Initiated context regeneration with instructions for {count} feasts.",
                    level=messages.SUCCESS
                )
                return redirect(reverse('admin:hub_feast_changelist'))

        context = dict(
            self.admin_site.each_context(request),
            opts=Feast._meta,
            title="Regenerate Feast Context with Instructions",
            feasts=feasts,
            ids=ids_param,
        )

        return TemplateResponse(
            request,
            "admin/hub/feast/regenerate_context_instructions.html",
            context
        )

    def church_link(self, feast):
        if not feast.day or not feast.day.church:
            return ""
        url = reverse("admin:hub_church_change", args=[feast.day.church.pk])
        return format_html('<a href="{}">{}</a>', url, feast.day.church.name)

    church_link.short_description = "Church"


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
        "short_text_preview",
    )
    list_display_links = (
        "feast",
        "prompt",
    )
    list_filter = ("active", "prompt__model", "feast__day__date")
    search_fields = ("text", "short_text", "feast__name")
    ordering = ("-time_of_generation",)
    raw_id_fields = ("feast", "prompt")
    readonly_fields = ("time_of_generation",)
    exclude = ("text", "short_text")  # Avoid duplicate with translation fields

    fieldsets = (
        (None, {
            'fields': ('feast', 'prompt', 'active', 'thumbs_up', 'thumbs_down', 'time_of_generation')
        }),
        ('Context Translations', {
            'description': (
                '<strong>Note:</strong> These contexts are auto-generated from the LLM prompt. '
                'The <code>text</code> field contains the detailed explanation, and <code>short_text</code> '
                'contains a 2-sentence summary. Both are generated in a single API call using JSON format.'
            ),
            'fields': ('text_en', 'text_hy', 'short_text_en', 'short_text_hy')
        }),
    )

    def text_preview(self, obj):
        return Truncator(obj.text).chars(100)

    text_preview.short_description = "Text Preview"

    def short_text_preview(self, obj):
        return Truncator(obj.short_text).chars(50)

    short_text_preview.short_description = "Short Text Preview"


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
