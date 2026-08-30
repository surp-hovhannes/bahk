"""Admin interface for the icons app."""
from django.contrib import admin

from bahk.admin_media import admin_thumbnail
from icons.models import Icon, IconFeedback


@admin.register(Icon)
class IconAdmin(admin.ModelAdmin):
    """Admin interface for Icon model."""
    
    list_display = ['title', 'image_preview', 'church', 'get_tag_list', 'image_hash', 'phash', 'created_at']
    list_filter = ['church', 'created_at', 'tags']
    search_fields = ['title', 'church__name', 'tags__name', 'image_hash', 'phash']
    readonly_fields = [
        'created_at',
        'updated_at',
        'cached_thumbnail_url',
        'cached_thumbnail_updated',
        'image_hash',
        'phash',
        'image_preview',
    ]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'church', 'tags')
        }),
        ('Image', {
            'fields': ('image', 'image_preview', 'cached_thumbnail_url', 'cached_thumbnail_updated')
        }),
        ('Footprints', {
            'fields': ('image_hash', 'phash'),
            'classes': ('collapse',)
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

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('church').prefetch_related('tags')

    @admin.display(description='Image')
    def image_preview(self, obj):
        return admin_thumbnail(
            obj,
            sources=('cached_thumbnail_url', 'thumbnail', 'image'),
            link_source='image',
            alt=f'Icon: {obj.title}' if obj else 'Icon preview',
            size='small',
            fallback='No image',
        )


@admin.register(IconFeedback)
class IconFeedbackAdmin(admin.ModelAdmin):
    """Admin interface for IconFeedback model."""

    list_display = ['icon_preview', 'icon_title', 'feedback_type', 'submitter_email', 'created_at', 'is_resolved']
    list_filter = ['feedback_type', 'is_resolved']
    date_hierarchy = 'created_at'
    search_fields = ['icon__title', 'description', 'submitter_email', 'admin_notes']

    readonly_fields = [
        'icon', 'feedback_type', 'description', 'suggested_tags',
        'submitter_email', 'icon_title_at_time', 'icon_tags_at_time',
        'created_at', 'http_user_agent', 'ip_address', 'icon_preview',
    ]

    fieldsets = (
        ('Submission Data', {
            'fields': (
                'icon', 'icon_preview', 'feedback_type', 'description', 'suggested_tags',
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

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('icon')

    @admin.display(description='Preview')
    def icon_preview(self, obj):
        icon = obj.icon if obj else None
        return admin_thumbnail(
            icon,
            sources=('cached_thumbnail_url', 'thumbnail', 'image'),
            link_source='image',
            alt=f'Icon: {icon.title}' if icon else 'Icon preview',
            size='small',
            fallback='No icon',
        )
