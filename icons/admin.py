"""Admin interface for the icons app."""
from django.contrib import admin
from icons.models import Icon, IconFeedback


@admin.register(Icon)
class IconAdmin(admin.ModelAdmin):
    """Admin interface for Icon model."""
    
    list_display = ['title', 'church', 'get_tag_list', 'created_at']
    list_filter = ['church', 'created_at', 'tags']
    search_fields = ['title', 'tags__name']
    readonly_fields = ['created_at', 'updated_at', 'cached_thumbnail_url', 'cached_thumbnail_updated']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'church', 'tags')
        }),
        ('Image', {
            'fields': ('image', 'cached_thumbnail_url', 'cached_thumbnail_updated')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_tag_list(self, obj):
        """Display tags as comma-separated list."""
        return ', '.join([tag.name for tag in obj.tags.all()])
    
    get_tag_list.short_description = 'Tags'


@admin.register(IconFeedback)
class IconFeedbackAdmin(admin.ModelAdmin):
    """Admin interface for IconFeedback model."""

    list_display = ['icon_title', 'feedback_type', 'submitter_email', 'created_at', 'is_resolved']
    list_filter = ['feedback_type', 'is_resolved']
    date_hierarchy = 'created_at'
    search_fields = ['icon__title', 'description', 'submitter_email', 'admin_notes']

    readonly_fields = [
        'icon', 'feedback_type', 'description', 'suggested_tags',
        'submitter_email', 'icon_title_at_time', 'icon_tags_at_time',
        'created_at', 'http_user_agent', 'ip_address',
    ]

    fieldsets = (
        ('Submission Data', {
            'fields': (
                'icon', 'feedback_type', 'description', 'suggested_tags',
                'submitter_email', 'icon_title_at_time', 'icon_tags_at_time',
            )
        }),
        ('Request Metadata', {
            'fields': ('http_user_agent', 'ip_address', 'created_at'),
            'classes': ('collapse',)
        }),
        ('Moderation', {
            'fields': ('is_resolved', 'resolved_at', 'admin_notes')
        }),
    )

    def icon_title(self, obj):
        return obj.icon.title
    icon_title.short_description = 'Icon'
    icon_title.admin_order_field = 'icon__title'
