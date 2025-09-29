from django.contrib import admin
from django.utils.html import format_html
from markdownx.admin import MarkdownxModelAdmin
from .models import Article, Recipe, Video, Bookmark

from django import forms
from s3_file_field.widgets import S3FileInput

class VideoAdminForm(forms.ModelForm):
    class Meta:
        model = Video
        fields = '__all__'
        widgets = {
            'video': S3FileInput(attrs={
                'accept': 'video/*'
            })
        }

@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    form = VideoAdminForm
    list_display = ('title', 'category', 'thumbnail_preview', 'created_at')
    search_fields = ('title', 'description')
    list_filter = ('category', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'thumbnail_preview')
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'category')
        }),
        ('Media', {
            'fields': ('video', 'thumbnail', 'thumbnail_preview'),
            'description': 'Large video files will be automatically uploaded in chunks. Please wait for the upload to complete.'
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def thumbnail_preview(self, obj):
        if obj.thumbnail:
            return format_html(
                '<img src="{}" style="max-height: 50px;"/>',
                obj.thumbnail_small.url
            )
        return "No thumbnail"
    thumbnail_preview.short_description = 'Thumbnail Preview'

@admin.register(Article)
class ArticleAdmin(MarkdownxModelAdmin):
    list_display = ('title', 'image_preview', 'created_at')
    search_fields = ('title', 'body')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    fieldsets = (
        (None, {
            'fields': ('title', 'body')
        }),
        ('Media', {
            'fields': ('image', 'image_preview')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 50px;"/>',
                obj.thumbnail.url
            )
        return "No image"
    image_preview.short_description = 'Image Preview'


@admin.register(Recipe)
class RecipeAdmin(MarkdownxModelAdmin):
    list_display = ('title', 'image_preview', 'created_at')
    search_fields = ('title', 'directions', 'ingredients')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'time_required', 'serves', 'directions', 'ingredients',),
        }),
        ('Media', {
            'fields': ('image', 'image_preview')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 50px;"/>',
                obj.thumbnail.url
            )
        return "No image"
    image_preview.short_description = 'Image Preview'


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    """Admin interface for managing user bookmarks."""
    
    list_display = (
        'user', 'content_type_name', 'content_title', 
        'object_id', 'created_at'
    )
    list_filter = ('content_type', 'created_at')
    search_fields = (
        'user__username', 'user__email', 'note'
    )
    readonly_fields = ('created_at', 'content_object_link')
    raw_id_fields = ('user',)  # Better performance for large user lists
    
    fieldsets = (
        (None, {
            'fields': ('user', 'content_type', 'object_id', 'content_object_link')
        }),
        ('Details', {
            'fields': ('note', 'created_at')
        })
    )
    
    def content_type_name(self, obj):
        """Display the content type in a readable format."""
        return obj.content_type.model.title().replace('_', ' ')
    content_type_name.short_description = 'Content Type'
    content_type_name.admin_order_field = 'content_type'
    
    def content_title(self, obj):
        """Display the title of the bookmarked content if available."""
        content = obj.content_object
        if content and hasattr(content, 'title'):
            # Check if the content is a translatable model
            if hasattr(content, 'safe_translation_getter'):
                return content.safe_translation_getter('title', any_language=True)
            else:
                return content.title
        elif content and hasattr(content, 'name'):
            # Check if the content is a translatable model
            if hasattr(content, 'safe_translation_getter'):
                return content.safe_translation_getter('name', any_language=True)
            else:
                return content.name
        return f"{obj.content_type.model} #{obj.object_id}"
    content_title.short_description = 'Content Title'
    
    def content_object_link(self, obj):
        """Display a link to the actual content object in admin."""
        content = obj.content_object
        if content:
            # Try to get the admin URL for the content object
            try:
                from django.urls import reverse
                url = reverse(
                    f'admin:{content._meta.app_label}_{content._meta.model_name}_change',
                    args=[content.pk]
                )
                return format_html(
                    '<a href="{}" target="_blank">{}</a>',
                    url,
                    content
                )
            except:
                return str(content)
        return "Content not found"
    content_object_link.short_description = 'Content Object'
    content_object_link.allow_tags = True
    
    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            'user', 'content_type'
        )
    
    def has_add_permission(self, request):
        """Allow admins to add bookmarks manually if needed."""
        return True
    
    def has_change_permission(self, request, obj=None):
        """Allow admins to modify bookmarks."""
        return True
    
    def has_delete_permission(self, request, obj=None):
        """Allow admins to delete bookmarks."""
        return True