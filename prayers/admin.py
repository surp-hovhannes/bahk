"""Admin interface for prayers app."""
from django.contrib import admin
from django.utils.html import format_html

from prayers.models import Prayer, PrayerSet, PrayerSetMembership

from adminsortable2.admin import SortableAdminBase, SortableInlineAdminMixin


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
    
    list_display = ('title', 'church', 'prayer_count', 'image_preview', 'created_at')
    list_filter = ('church', 'created_at', 'updated_at')
    search_fields = ('title', 'description')
    raw_id_fields = ('church',)
    readonly_fields = ('created_at', 'updated_at', 'image_preview', 'prayer_count')
    inlines = [PrayerSetMembershipInline]
    
    fieldsets = (
        (None, {
            'fields': ('title', 'title_hy', 'description', 'description_hy', 'church')
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

