"""Admin interface for prayers app."""

import json
import logging
import uuid

from django import forms
from django.contrib import admin, messages
from django.core.cache import cache
from django.core.validators import FileExtensionValidator
from django.db import models
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from adminsortable2.admin import SortableAdminBase, SortableInlineAdminMixin
from bahk.admin_media import admin_thumbnail
from events.models import Event, EventType, UserMilestone
from hub.models import Church
from prayers.import_utils import (
    detect_conflicts,
    execute_import,
    get_import_counts,
    validate_import_json,
)
from prayers.models import (
    Prayer,
    FeastPrayer,
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
    PrayerSet,
    PrayerSetMembership,
)
from prayers.tasks import match_icons_for_imported_prayers_task

logger = logging.getLogger(__name__)

MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


class PrayerSetImportForm(forms.Form):
    """Admin form for importing prayer sets from JSON."""

    church = forms.ModelChoiceField(queryset=Church.objects.all(), required=True)
    json_file = forms.FileField(
        required=True,
        validators=[FileExtensionValidator(allowed_extensions=["json"])],
    )
    use_ai_icon_matching = forms.BooleanField(
        required=False,
        initial=False,
        label="Use AI icon matching",
    )

    def clean_json_file(self):
        uploaded = self.cleaned_data.get("json_file")
        if uploaded:
            if uploaded.size > MAX_IMPORT_FILE_BYTES:
                raise forms.ValidationError(
                    f"File too large ({uploaded.size} bytes). Maximum is {MAX_IMPORT_FILE_BYTES} bytes."
                )
        return uploaded


class PrayerSetMembershipInline(SortableInlineAdminMixin, admin.TabularInline):
    """Inline admin for managing prayers within a prayer set."""

    model = PrayerSetMembership
    extra = 1
    fields = ("prayer", "order")
    autocomplete_fields = ("prayer",)
    ordering = ("order",)


@admin.register(Prayer)
class PrayerAdmin(admin.ModelAdmin):
    """Admin interface for Prayer model."""

    list_display = ("title", "category", "church", "fast", "video", "icon_preview", "tag_list", "created_at")
    list_filter = ("church", "category", "fast", "created_at", "tags")
    search_fields = ("title", "text")
    autocomplete_fields = ("church", "fast", "video", "icon")
    readonly_fields = ("created_at", "updated_at", "icon_preview")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("church", "fast", "video", "icon").prefetch_related("tags")

    actions = ["match_icons_with_ai"]

    @admin.action(description="Match icons with AI for selected prayers")
    def match_icons_with_ai(self, request, queryset):
        """Run AI icon matching on selected prayers."""
        if not queryset.exists():
            self.message_user(request, "No prayers selected.", messages.WARNING)
            return
        church_ids = set(queryset.values_list("church_id", flat=True).distinct())
        for church_id in church_ids:
            church_prayers = queryset.filter(church_id=church_id)
            prayer_ids = list(church_prayers.values_list("id", flat=True))
            match_icons_for_imported_prayers_task.delay(prayer_ids, church_id)
        self.message_user(
            request,
            f"Icon matching scheduled for {queryset.count()} prayer(s) across {len(church_ids)} church(es).",
            messages.SUCCESS,
        )

    fieldsets = (
        (None, {"fields": ("title", "title_hy", "text", "text_hy", "category")}),
        ("Media", {"fields": ("video", "icon", "icon_preview")}),
        ("Organization", {"fields": ("church", "fast", "tags")}),
        ("Metadata", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def tag_list(self, obj):
        """Display tags as comma-separated list."""
        return ", ".join(tag.name for tag in obj.tags.all())

    tag_list.short_description = "Tags"

    def icon_preview(self, obj):
        """Display the selected icon thumbnail."""
        icon = obj.icon if obj else None
        return admin_thumbnail(
            icon,
            sources=("cached_thumbnail_url", "thumbnail", "image"),
            link_source="image",
            alt=f"Icon for {obj.title}" if obj else "Prayer icon",
            size="small",
            fallback="No icon",
                )

    icon_preview.short_description = "Icon"


@admin.register(PrayerSet)
class PrayerSetAdmin(SortableAdminBase, admin.ModelAdmin):
    """Admin interface for PrayerSet model."""

    change_list_template = "admin/prayers/prayerset/change_list.html"
    list_display = ("title", "category", "church", "prayer_count", "image_preview", "created_at")
    list_filter = ("church", "category", "created_at", "updated_at")
    search_fields = ("title", "description")
    autocomplete_fields = ("church", "icon")
    readonly_fields = ("created_at", "updated_at", "image_preview", "prayer_count", "icon_preview")
    inlines = [PrayerSetMembershipInline]

    fieldsets = (
        (None, {"fields": ("title", "title_hy", "description", "description_hy", "category", "church")}),
        ("Media", {"fields": ("image", "image_preview", "icon", "icon_preview")}),
        ("Statistics", {"fields": ("prayer_count",), "classes": ("collapse",)}),
        ("Metadata", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("church", "icon")
            .annotate(_admin_prayer_count=models.Count("memberships", distinct=True))
        )

    def image_preview(self, obj):
        """Display a thumbnail preview of the image."""
        return admin_thumbnail(
            obj,
            sources=("cached_thumbnail_url", "thumbnail", "image"),
            link_source="image",
            alt=f"Image for {obj.title}" if obj else "Prayer set image",
            fallback="No image",
            )

    image_preview.short_description = "Image Preview"

    def icon_preview(self, obj):
        """Display the selected icon thumbnail."""
        icon = obj.icon if obj else None
        return admin_thumbnail(
            icon,
            sources=("cached_thumbnail_url", "thumbnail", "image"),
            link_source="image",
            alt=f"Icon for {obj.title}" if obj else "Prayer set icon",
            size="small",
            fallback="No icon",
            )

    icon_preview.short_description = "Icon"

    @admin.display(description="Number of Prayers", ordering="_admin_prayer_count")
    def prayer_count(self, obj):
        """Return the number of prayers in this set."""
        if hasattr(obj, "_admin_prayer_count"):
            return obj._admin_prayer_count
        return obj.prayers.count()

    def get_urls(self):
        """Add prayer set JSON import admin views."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="prayers_import",
            ),
            path(
                "import/confirm/",
                self.admin_site.admin_view(self.import_confirm_view),
                name="prayers_import_confirm",
            ),
        ]
        return custom_urls + urls

    def import_view(self, request):
        """Handle prayer set import upload and preview."""
        if request.method == "POST":
            form = PrayerSetImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    data = json.load(form.cleaned_data["json_file"])
                    validate_import_json(data)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    form.add_error("json_file", str(exc))
                else:
                    church = form.cleaned_data["church"]
                    conflicts = detect_conflicts(data, church)
                    context = {
                        **self.admin_site.each_context(request),
                        "title": "Import Prayer Sets",
                        "opts": self.model._meta,
                        "form": form,
                        "church": church,
                        "counts": get_import_counts(data),
                        "prayer_sets": data["prayer_sets"],
                        "conflicts": conflicts,
                    }
                    if conflicts:
                        if "prayer_import" in request.session:
                            del request.session["prayer_import"]
                            request.session.modified = True
                        return render(
                            request,
                            "admin/prayers/import_conflicts.html",
                            context,
                        )

                    request.session["prayer_import"] = {
                        "data": data,
                        "church_id": church.id,
                        "use_ai_icon_matching": form.cleaned_data["use_ai_icon_matching"],
                        "import_id": str(uuid.uuid4()),
                    }
                    request.session.modified = True
                    return render(
                        request,
                        "admin/prayers/import_preview.html",
                        context,
                    )
        else:
            form = PrayerSetImportForm()

        return render(
            request,
            "admin/prayers/import_form.html",
            {
                **self.admin_site.each_context(request),
                "title": "Import Prayer Sets",
                "opts": self.model._meta,
                "form": form,
            },
        )

    def import_confirm_view(self, request):
        """Execute a previously previewed prayer set import."""
        if request.method != "POST":
            return redirect(reverse("admin:prayers_import"))

        import_state = request.session.get("prayer_import")
        if not import_state:
            messages.error(request, "No prayer import is ready to confirm.")
            return redirect(reverse("admin:prayers_import"))

        try:
            church = Church.objects.get(id=import_state["church_id"])
        except Church.DoesNotExist:
            messages.error(request, "The selected church no longer exists. Please start a new import.")
            request.session.pop("prayer_import", None)
            request.session.modified = True
            return redirect(reverse("admin:prayers_import"))

        data = import_state["data"]
        try:
            validate_import_json(data)
        except ValueError as exc:
            request.session.pop("prayer_import", None)
            request.session.modified = True
            messages.error(request, str(exc))
            return redirect(reverse("admin:prayers_import"))
        conflicts = detect_conflicts(data, church)
        if conflicts:
            request.session.pop("prayer_import", None)
            request.session.modified = True
            messages.error(
                request,
                "Import blocked because title conflicts were detected. Resolve conflicts and start a new import.",
            )
            return redirect(reverse("admin:prayers_import"))

        import_id = import_state.get("import_id")
        import_cache_key = f"prayer_import:{import_id}" if import_id else None
        if import_cache_key and not cache.add(import_cache_key, "in_progress", timeout=3600):
            if cache.get(import_cache_key) == "completed":
                messages.error(request, "This import has already been submitted.")
            else:
                messages.error(
                    request,
                    "This import is already in progress. Please wait or try again shortly.",
                )
            return redirect(reverse("admin:prayers_import"))

        try:
            sets_created, prayers_created, created_prayer_ids = execute_import(data, church)
        except ValueError as exc:
            if import_cache_key:
                cache.delete(import_cache_key)
            request.session.pop("prayer_import", None)
            request.session.modified = True
            messages.error(request, str(exc))
            return redirect(reverse("admin:prayers_import"))
        except Exception:
            if import_cache_key:
                cache.delete(import_cache_key)
            messages.error(request, "Import failed. Please try confirming again.")
            return redirect(reverse("admin:prayers_import"))

        if import_cache_key:
            cache.set(import_cache_key, "completed", timeout=3600)

        request.session.pop("prayer_import", None)
        request.session.modified = True

        if import_state.get("use_ai_icon_matching") and created_prayer_ids:
            try:
                match_icons_for_imported_prayers_task.delay(created_prayer_ids, church.id)
            except Exception:
                logger.exception("Failed to enqueue icon matching task after prayer import")
                messages.warning(
                    request,
                    "Import succeeded but AI icon matching could not be scheduled.",
                )

        messages.success(
            request,
            f"Imported {sets_created} prayer set(s) and {prayers_created} prayer(s).",
        )
        return redirect(reverse("admin:prayers_prayerset_changelist"))


# Note: PrayerSetMembership is not registered as a standalone admin
# It's managed through the inline admin in PrayerSetAdmin

# Prayer Request Admin


class PrayerRequestAttentionFilter(admin.SimpleListFilter):
    """Task-oriented moderation and lifecycle filters."""

    title = "attention"
    parameter_name = "attention"

    def lookups(self, request, model_admin):
        return (
            ("needs_review", "Needs review"),
            ("pending", "Pending moderation"),
            ("expired_active", "Expired but still approved"),
            ("resolved", "Resolved"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "needs_review":
            return queryset.filter(
                models.Q(requires_human_review=True)
                | models.Q(status="pending_moderation", reviewed=False)
            )
        if value == "pending":
            return queryset.filter(status="pending_moderation")
        if value == "expired_active":
            return queryset.filter(
                status="approved", expiration_date__lte=timezone.now()
            )
        if value == "resolved":
            return queryset.filter(status__in=("completed", "rejected", "deleted"))
        return queryset


@admin.register(PrayerRequest)
class PrayerRequestAdmin(admin.ModelAdmin):
    """Admin interface for PrayerRequest model."""

    list_display = (
        "image_preview",
        "title",
        "requester",
        "moderation_state",
        "expiration_state",
        "acceptance_count",
        "created_at",
    )
    list_display_links = ("title",)
    list_filter = (
        PrayerRequestAttentionFilter,
        "status",
        "moderation_severity",
        "requires_human_review",
        "reviewed",
        "is_anonymous",
        "duration_days",
        "created_at",
    )
    search_fields = ("title", "description", "requester__email", "requester__first_name", "requester__last_name")
    autocomplete_fields = ("requester", "icon")
    date_hierarchy = "created_at"
    list_per_page = 50
    readonly_fields = (
        "expiration_date",
        "reviewed",
        "moderated_at",
        "moderation_severity",
        "moderation_result_display",
        "image_preview",
        "acceptance_count",
        "prayer_log_count",
        "created_at",
        "updated_at",
    )
    actions = ["approve_requests", "reject_requests", "mark_manually_reviewed"]

    fieldsets = (
        (None, {"fields": ("title", "description", "requester", "is_anonymous")}),
        ("Duration", {"fields": ("duration_days", "expiration_date")}),
        ("Media", {"fields": ("image", "icon", "image_preview")}),
        (
            "Moderation",
            {
                "fields": (
                    "status",
                    "moderation_severity",
                    "requires_human_review",
                    "reviewed",
                    "moderated_at",
                    "moderation_result_display",
                )
            },
        ),
        ("Statistics", {"fields": ("acceptance_count", "prayer_log_count"), "classes": ("collapse",)}),
        ("Metadata", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "requester", "icon"
        ).annotate(
            _admin_acceptance_count=models.Count("acceptances", distinct=True),
            _admin_prayer_log_count=models.Count("prayer_logs", distinct=True),
        )

    def image_preview(self, obj):
        """Display a thumbnail preview of the image."""
        preview = admin_thumbnail(
            obj,
            sources=("cached_thumbnail_url", "thumbnail", "image"),
            link_source="image",
            alt=f"Image for {obj.title}" if obj else "Prayer request image",
            fallback="",
        )
        if preview:
            return preview
        icon = obj.icon if obj else None
        return admin_thumbnail(
            icon,
            sources=("cached_thumbnail_url", "thumbnail", "image"),
            link_source="image",
            alt=f"Fallback icon for {obj.title}" if obj else "Prayer request icon",
            size="small",
            fallback="No image",
        )

    image_preview.short_description = "Image Preview"

    @admin.display(description="Moderation")
    def moderation_state(self, obj):
        badges = [("neutral", obj.get_status_display())]
        if obj.moderation_severity:
            badges.append(
                (
                    "critical" if obj.moderation_severity in {"high", "critical"} else "warning",
                    obj.get_moderation_severity_display(),
                )
            )
        if obj.requires_human_review:
            badges.append(("critical", "Human review"))
        elif obj.reviewed:
            badges.append(("positive", "Reviewed"))
        return format_html_join(
            " ",
            '<span class="fp-admin-state fp-admin-state--{}">{}</span>',
            badges,
        )

    @admin.display(description="Expiration", ordering="expiration_date")
    def expiration_state(self, obj):
        if obj.status in {"completed", "rejected", "deleted"}:
            label = obj.get_status_display()
            tone = "neutral"
        elif obj.expiration_date <= timezone.now():
            label = "Expired"
            tone = "critical"
        else:
            label = f"Active until {obj.expiration_date:%b %d, %Y}"
            tone = "positive"
        return format_html(
            '<span class="fp-admin-state fp-admin-state--{}">{}</span>',
            tone,
            label,
        )

    def moderation_result_display(self, obj):
        """Display moderation result as formatted JSON."""
        if obj.moderation_result:
            formatted_json = json.dumps(obj.moderation_result, indent=2)
            return format_html("<pre>{}</pre>", formatted_json)
        return "No moderation result"

    moderation_result_display.short_description = "Moderation Result"

    def acceptance_count(self, obj):
        """Return the number of acceptances."""
        if hasattr(obj, "_admin_acceptance_count"):
            return obj._admin_acceptance_count
        return obj.get_acceptance_count()

    acceptance_count.short_description = "Acceptances"
    acceptance_count.admin_order_field = "_admin_acceptance_count"

    def prayer_log_count(self, obj):
        """Return the total number of prayer logs."""
        if hasattr(obj, "_admin_prayer_log_count"):
            return obj._admin_prayer_log_count
        return obj.get_prayer_log_count()

    prayer_log_count.short_description = "Prayer Logs"

    def approve_requests(self, request, queryset):
        """Bulk action to approve prayer requests.

        This matches automated moderation behavior by:
        - Setting moderated_at timestamp
        - Clearing requires_human_review flag
        - Creating PRAYER_REQUEST_CREATED events
        - Checking for first_prayer_request_created milestones
        - Auto-accepting requester's own prayer request
        """
        pending_requests = queryset.filter(status="pending_moderation")
        count = 0
        now = timezone.now()

        for prayer_request in pending_requests:
            # Update fields to match automated moderation behavior
            prayer_request.status = "approved"
            prayer_request.reviewed = True
            prayer_request.moderated_at = now
            prayer_request.requires_human_review = False
            prayer_request.save()

            # Create event for approved prayer request
            Event.create_event(
                event_type_code=EventType.PRAYER_REQUEST_CREATED,
                user=prayer_request.requester,
                target=prayer_request,
                title=f"Prayer request created: {prayer_request.title}",
                data={
                    "prayer_request_id": prayer_request.id,
                    "is_anonymous": prayer_request.is_anonymous,
                },
            )

            # Check for first prayer request milestone
            if prayer_request.requester.prayer_requests.filter(status="approved").count() == 1:
                UserMilestone.create_milestone(
                    user=prayer_request.requester,
                    milestone_type="first_prayer_request_created",
                    related_object=prayer_request,
                    data={
                        "prayer_request_id": prayer_request.id,
                        "title": prayer_request.title,
                    },
                )

            # Automatically accept own prayer request
            PrayerRequestAcceptance.objects.get_or_create(
                prayer_request=prayer_request, user=prayer_request.requester, defaults={"counts_for_milestones": False}
            )

            count += 1

        self.message_user(request, f"{count} prayer request(s) approved.")

    approve_requests.short_description = "Approve selected prayer requests"

    def reject_requests(self, request, queryset):
        """Bulk action to reject prayer requests.

        This matches automated moderation behavior by:
        - Setting moderated_at timestamp
        - Clearing requires_human_review flag
        """
        pending_requests = queryset.filter(status="pending_moderation")
        count = 0
        now = timezone.now()

        for prayer_request in pending_requests:
            # Update fields to match automated moderation behavior
            prayer_request.status = "rejected"
            prayer_request.reviewed = True
            prayer_request.moderated_at = now
            prayer_request.requires_human_review = False
            prayer_request.save()
            count += 1

        self.message_user(request, f"{count} prayer request(s) rejected.")

    reject_requests.short_description = "Reject selected prayer requests"

    def mark_manually_reviewed(self, request, queryset):
        """Bulk action to mark prayer requests as manually reviewed.

        This clears the requires_human_review flag and marks as reviewed.
        """
        count = queryset.update(requires_human_review=False, reviewed=True)

        self.message_user(request, f"{count} prayer request(s) marked as manually reviewed.")

    mark_manually_reviewed.short_description = "Mark as manually reviewed"


@admin.register(PrayerRequestAcceptance)
class PrayerRequestAcceptanceAdmin(admin.ModelAdmin):
    """Admin interface for PrayerRequestAcceptance model."""

    list_display = ("prayer_request", "user", "accepted_at")
    list_filter = ("accepted_at",)
    search_fields = ("prayer_request__title", "user__email", "user__first_name", "user__last_name")
    autocomplete_fields = ("prayer_request", "user")
    readonly_fields = ("accepted_at",)
    date_hierarchy = "accepted_at"

    fieldsets = ((None, {"fields": ("prayer_request", "user", "accepted_at")}),)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("prayer_request", "user")


@admin.register(PrayerRequestPrayerLog)
class PrayerRequestPrayerLogAdmin(admin.ModelAdmin):
    """Admin interface for PrayerRequestPrayerLog model."""

    list_display = ("prayer_request", "user", "prayed_on_date", "created_at")
    list_filter = ("prayed_on_date", "created_at")
    search_fields = ("prayer_request__title", "user__email", "user__first_name", "user__last_name")
    autocomplete_fields = ("prayer_request", "user")
    readonly_fields = ("created_at",)
    date_hierarchy = "prayed_on_date"

    fieldsets = ((None, {"fields": ("prayer_request", "user", "prayed_on_date", "created_at")}),)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("prayer_request", "user")


@admin.register(FeastPrayer)
class FeastPrayerAdmin(admin.ModelAdmin):
    """Admin interface for FeastPrayer model."""

    list_display = ("designation_short", "title_preview", "created_at")
    search_fields = ("designation", "title", "text")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("designation",)}),
        (
            "English Prayer Template",
            {"fields": ("title", "text"), "description": "Use {feast_name} placeholder for feast name."},
        ),
        (
            "Armenian Translation",
            {
                "fields": ("title_hy", "text_hy"),
                "classes": ("collapse",),
            },
        ),
        ("Metadata", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Designation", ordering="designation")
    def designation_short(self, obj):
        """Display shortened designation."""
        return obj.designation[:50] + "..." if len(obj.designation) > 50 else obj.designation

    @admin.display(description="Title", ordering="title")
    def title_preview(self, obj):
        """Display prayer title with placeholder hint."""
        title = obj.title or ""
        if "{feast_name}" in title:
            return format_html('{} <span style="color: #999;">(has placeholder)</span>', title[:50])
        return title[:50]

    title_preview.short_description = "Title"
