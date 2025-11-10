"""Admin interface for the icons app."""
from django.contrib import admin
from icons.models import Icon


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
