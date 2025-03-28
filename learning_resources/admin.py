from django.contrib import admin
from django.utils.html import format_html
from markdownx.admin import MarkdownxModelAdmin
from .models import Article, Recipe, Video

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
