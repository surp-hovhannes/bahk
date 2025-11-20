"""Admin interface for prayers app."""
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
import json

from prayers.models import (
    Prayer,
    PrayerSet,
    PrayerSetMembership,
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
)

from adminsortable2.admin import SortableAdminBase, SortableInlineAdminMixin
from events.models import Event, EventType, UserMilestone


class PrayerSetMembershipInline(SortableInlineAdminMixin, admin.TabularInline):
    """Inline admin for managing prayers within a prayer set."""
    model = PrayerSetMembership
    extra = 1
    fields = ('prayer', 'order')
    raw_id_fields = ('prayer',)
    ordering = ('order',)


@admin.register(Prayer)
class PrayerAdmin(admin.ModelAdmin):
    """Admin interface for Prayer model."""
    
    list_display = ('title', 'category', 'church', 'fast', 'tag_list', 'created_at')
    list_filter = ('church', 'category', 'fast', 'created_at', 'tags')
    search_fields = ('title', 'text')
    raw_id_fields = ('church', 'fast')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        (None, {
            'fields': ('title', 'title_hy', 'text', 'text_hy', 'category')
        }),
        ('Organization', {
            'fields': ('church', 'fast', 'tags')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def tag_list(self, obj):
        """Display tags as comma-separated list."""
        return ', '.join(tag.name for tag in obj.tags.all())
    
    tag_list.short_description = 'Tags'


@admin.register(PrayerSet)
class PrayerSetAdmin(SortableAdminBase, admin.ModelAdmin):
    """Admin interface for PrayerSet model."""
    
    list_display = ('title', 'category', 'church', 'prayer_count', 'image_preview', 'created_at')
    list_filter = ('church', 'category', 'created_at', 'updated_at')
    search_fields = ('title', 'description')
    raw_id_fields = ('church',)
    readonly_fields = ('created_at', 'updated_at', 'image_preview', 'prayer_count')
    inlines = [PrayerSetMembershipInline]
    
    fieldsets = (
        (None, {
            'fields': ('title', 'title_hy', 'description', 'description_hy', 'category', 'church')
        }),
        ('Media', {
            'fields': ('image', 'image_preview')
        }),
        ('Statistics', {
            'fields': ('prayer_count',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def image_preview(self, obj):
        """Display a thumbnail preview of the image."""
        if obj.cached_thumbnail_url:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.cached_thumbnail_url
            )
        elif obj.image:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.image.url
            )
        return 'No image'
    
    image_preview.short_description = 'Image Preview'
    
    def prayer_count(self, obj):
        """Return the number of prayers in this set."""
        return obj.prayers.count()
    
    prayer_count.short_description = 'Number of Prayers'


# Note: PrayerSetMembership is not registered as a standalone admin
# It's managed through the inline admin in PrayerSetAdmin

# Prayer Request Admin


@admin.register(PrayerRequest)
class PrayerRequestAdmin(admin.ModelAdmin):
    """Admin interface for PrayerRequest model."""

    list_display = (
        'title', 'requester', 'status', 'reviewed', 'is_anonymous',
        'duration_days', 'expiration_date', 'acceptance_count', 'created_at'
    )
    list_filter = ('status', 'reviewed', 'is_anonymous', 'duration_days', 'created_at')
    search_fields = ('title', 'description', 'requester__email', 'requester__first_name', 'requester__last_name')
    raw_id_fields = ('requester',)
    readonly_fields = (
        'expiration_date', 'reviewed', 'moderated_at', 'moderation_result_display',
        'image_preview', 'acceptance_count', 'prayer_log_count', 'created_at', 'updated_at'
    )
    actions = ['approve_requests', 'reject_requests']

    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'requester', 'is_anonymous')
        }),
        ('Duration', {
            'fields': ('duration_days', 'expiration_date')
        }),
        ('Media', {
            'fields': ('image', 'image_preview')
        }),
        ('Moderation', {
            'fields': ('status', 'reviewed', 'moderated_at', 'moderation_result_display')
        }),
        ('Statistics', {
            'fields': ('acceptance_count', 'prayer_log_count'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def image_preview(self, obj):
        """Display a thumbnail preview of the image."""
        if obj.cached_thumbnail_url:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.cached_thumbnail_url
            )
        elif obj.image:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.image.url
            )
        return 'No image'

    image_preview.short_description = 'Image Preview'

    def moderation_result_display(self, obj):
        """Display moderation result as formatted JSON."""
        if obj.moderation_result:
            formatted_json = json.dumps(obj.moderation_result, indent=2)
            return format_html('<pre>{}</pre>', formatted_json)
        return 'No moderation result'

    moderation_result_display.short_description = 'Moderation Result'

    def acceptance_count(self, obj):
        """Return the number of acceptances."""
        return obj.get_acceptance_count()

    acceptance_count.short_description = 'Acceptances'

    def prayer_log_count(self, obj):
        """Return the total number of prayer logs."""
        return obj.get_prayer_log_count()

    prayer_log_count.short_description = 'Prayer Logs'

    def approve_requests(self, request, queryset):
        """Bulk action to approve prayer requests.
        
        This matches automated moderation behavior by:
        - Setting moderated_at timestamp
        - Creating PRAYER_REQUEST_CREATED events
        - Checking for first_prayer_request_created milestones
        - Auto-accepting requester's own prayer request
        """
        pending_requests = queryset.filter(status='pending_moderation')
        count = 0
        now = timezone.now()
        
        for prayer_request in pending_requests:
            # Update all three fields to match automated moderation
            prayer_request.status = 'approved'
            prayer_request.reviewed = True
            prayer_request.moderated_at = now
            prayer_request.save()
            
            # Create event for approved prayer request
            Event.create_event(
                event_type_code=EventType.PRAYER_REQUEST_CREATED,
                user=prayer_request.requester,
                target=prayer_request,
                title=f'Prayer request created: {prayer_request.title}',
                data={
                    'prayer_request_id': prayer_request.id,
                    'is_anonymous': prayer_request.is_anonymous,
                }
            )
            
            # Check for first prayer request milestone
            if prayer_request.requester.prayer_requests.filter(
                status='approved'
            ).count() == 1:
                UserMilestone.create_milestone(
                    user=prayer_request.requester,
                    milestone_type='first_prayer_request_created',
                    related_object=prayer_request,
                    data={
                        'prayer_request_id': prayer_request.id,
                        'title': prayer_request.title,
                    }
                )
            
            # Automatically accept own prayer request
            PrayerRequestAcceptance.objects.get_or_create(
                prayer_request=prayer_request,
                user=prayer_request.requester,
                defaults={'counts_for_milestones': False}
            )
            
            count += 1
        
        self.message_user(request, f'{count} prayer request(s) approved.')

    approve_requests.short_description = 'Approve selected prayer requests'

    def reject_requests(self, request, queryset):
        """Bulk action to reject prayer requests.
        
        This matches automated moderation behavior by:
        - Setting moderated_at timestamp
        """
        pending_requests = queryset.filter(status='pending_moderation')
        count = 0
        now = timezone.now()
        
        for prayer_request in pending_requests:
            # Update all three fields to match automated moderation
            prayer_request.status = 'rejected'
            prayer_request.reviewed = True
            prayer_request.moderated_at = now
            prayer_request.save()
            count += 1
        
        self.message_user(request, f'{count} prayer request(s) rejected.')

    reject_requests.short_description = 'Reject selected prayer requests'


@admin.register(PrayerRequestAcceptance)
class PrayerRequestAcceptanceAdmin(admin.ModelAdmin):
    """Admin interface for PrayerRequestAcceptance model."""

    list_display = ('prayer_request', 'user', 'accepted_at')
    list_filter = ('accepted_at',)
    search_fields = (
        'prayer_request__title', 'user__email',
        'user__first_name', 'user__last_name'
    )
    raw_id_fields = ('prayer_request', 'user')
    readonly_fields = ('accepted_at',)

    fieldsets = (
        (None, {
            'fields': ('prayer_request', 'user', 'accepted_at')
        }),
    )


@admin.register(PrayerRequestPrayerLog)
class PrayerRequestPrayerLogAdmin(admin.ModelAdmin):
    """Admin interface for PrayerRequestPrayerLog model."""

    list_display = ('prayer_request', 'user', 'prayed_on_date', 'created_at')
    list_filter = ('prayed_on_date', 'created_at')
    search_fields = (
        'prayer_request__title', 'user__email',
        'user__first_name', 'user__last_name'
    )
    raw_id_fields = ('prayer_request', 'user')
    readonly_fields = ('created_at',)
    date_hierarchy = 'prayed_on_date'

    fieldsets = (
        (None, {
            'fields': ('prayer_request', 'user', 'prayed_on_date', 'created_at')
        }),
    )
