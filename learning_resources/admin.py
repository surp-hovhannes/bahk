from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html
from markdownx.admin import MarkdownxModelAdmin

from bahk.admin_media import admin_thumbnail, admin_video_player
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
    list_display = ('title', 'category', 'language_code', 'thumbnail_preview', 'created_at')
    search_fields = ('title', 'description')
    list_filter = ('category', 'language_code', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'thumbnail_preview', 'video_preview')
    # Hide base fields that also have modeltrans virtual translation fields to
    # avoid duplicate inputs in the admin form
    exclude = ('title', 'description')

    def thumbnail_preview(self, obj):
        return admin_thumbnail(
            obj,
            sources=('cached_thumbnail_url', 'thumbnail_small', 'thumbnail'),
            link_source='thumbnail',
            alt=f'Thumbnail for {obj.title}' if obj else 'Video thumbnail',
            size='portrait',
            fallback='No thumbnail',
        )
    thumbnail_preview.short_description = 'Thumbnail Preview'

    def video_preview(self, obj):
        return admin_video_player(
            obj,
            source='video',
            title=f'Video preview for {obj.title}' if obj else 'Video preview',
            poster_sources=('cached_thumbnail_url', 'thumbnail_small', 'thumbnail'),
            fallback='Upload and save a video to preview it here.',
        )
    video_preview.short_description = 'Watch Video'

    def get_fields(self, request, obj=None):
        """Keep media previews next to the fields they describe."""
        fields = list(super().get_fields(request, obj))
        for source, preview in (
            ('thumbnail', 'thumbnail_preview'),
            ('video', 'video_preview'),
        ):
            if preview not in fields:
                continue
            fields.remove(preview)
            if source in fields:
                fields.insert(fields.index(source) + 1, preview)
            else:
                fields.append(preview)
        return fields

@admin.register(Article)
class ArticleAdmin(MarkdownxModelAdmin):
    list_display = ('title', 'image_preview', 'created_at')
    search_fields = ('title', 'body')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    # Hide base fields that also have modeltrans virtual translation fields to
    # avoid duplicate inputs in the admin form
    exclude = ('title', 'body')

    def image_preview(self, obj):
        return admin_thumbnail(
            obj,
            sources=('cached_thumbnail_url', 'thumbnail', 'image'),
            link_source='image',
            alt=f'Image for {obj.title}' if obj else 'Article image',
            fallback='No image',
        )
    image_preview.short_description = 'Image Preview'


@admin.register(Recipe)
class RecipeAdmin(MarkdownxModelAdmin):
    list_display = ('title', 'image_preview', 'created_at')
    search_fields = ('title', 'directions', 'ingredients')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    # Hide base fields that also have modeltrans virtual translation fields to
    # avoid duplicate inputs in the admin formx
    exclude = ('title', 'description', 'time_required', 'serves', 'directions', 'ingredients')

    def image_preview(self, obj):
        return admin_thumbnail(
            obj,
            sources=('cached_thumbnail_url', 'thumbnail', 'image'),
            link_source='image',
            alt=f'Image for {obj.title}' if obj else 'Recipe image',
            fallback='No image',
        )
    image_preview.short_description = 'Image Preview'


class BookmarkContentTypeFilter(admin.SimpleListFilter):
    title = 'content type'
    parameter_name = 'content_type'

    def lookups(self, request, model_admin):
        content_type_ids = (
            model_admin.get_queryset(request)
            .order_by()
            .values_list('content_type_id', flat=True)
            .distinct()
        )
        return [
            (content_type.pk, f'{content_type.app_label} | {content_type.model}')
            for content_type in ContentType.objects.filter(pk__in=content_type_ids).order_by(
                'app_label', 'model'
            )
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(content_type_id=self.value())
        return queryset


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    """Admin interface for managing user bookmarks."""
    
    list_display = (
        'user', 'content_type_name', 'content_title', 
        'object_id', 'created_at'
    )
    list_filter = (BookmarkContentTypeFilter, 'created_at')
    search_fields = (
        'user__username', 'user__email', 'note'
    )
    readonly_fields = ('created_at', 'content_object_link')
    autocomplete_fields = ('user',)
    
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
            return content.title
        elif content and hasattr(content, 'name'):
            return content.name
        return f"{obj.content_type.model} #{obj.object_id}"
    content_title.short_description = 'Content Title'
    
    def content_object_link(self, obj):
        """Display a link to the actual content object in admin."""
        content = obj.content_object
        if content:
            # Try to get the admin URL for the content object
            try:
                url = reverse(
                    f'admin:{content._meta.app_label}_{content._meta.model_name}_change',
                    args=[content.pk]
                )
                return format_html(
                    '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    url,
                    content
                )
            except Exception:
                return str(content)
        return "Content not found"
    content_object_link.short_description = 'Content Object'
    content_object_link.allow_tags = True
    
    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            'user', 'content_type'
        ).prefetch_related('content_object')
    
    def has_add_permission(self, request):
        """Allow admins to add bookmarks manually if needed."""
        return True
    
    def has_change_permission(self, request, obj=None):
        """Allow admins to modify bookmarks."""
        return True
    
    def has_delete_permission(self, request, obj=None):
        """Allow admins to delete bookmarks."""
        return True
