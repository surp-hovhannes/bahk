from django.contrib import admin
from django.utils.html import format_html
from markdownx.admin import MarkdownxModelAdmin
from .models import Article, Recipe, Video, VideoTranslation, ArticleTranslation, RecipeTranslation

from django import forms
from s3_file_field.widgets import S3FileInput


# Inline admin classes for translations
class VideoTranslationInline(admin.StackedInline):
    model = VideoTranslation
    extra = 0
    fields = ('language', 'title', 'description')
    verbose_name = 'Translation'
    verbose_name_plural = 'Translations'


class ArticleTranslationInline(admin.StackedInline):
    model = ArticleTranslation
    extra = 0
    fields = ('language', 'title', 'body')
    verbose_name = 'Translation'
    verbose_name_plural = 'Translations'


class RecipeTranslationInline(admin.StackedInline):
    model = RecipeTranslation
    extra = 0
    fields = ('language', 'title', 'description', 'time_required', 'serves', 'ingredients', 'directions')
    verbose_name = 'Translation'
    verbose_name_plural = 'Translations'


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
    list_display = ('title', 'category', 'thumbnail_preview', 'created_at', 'translations')
    search_fields = ('title', 'description')
    list_filter = ('category', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'thumbnail_preview')
    inlines = [VideoTranslationInline]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'category'),
            'description': 'Default content (usually English). Add translations below.'
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

    def translations(self, obj):
        return ", ".join(sorted([translation.get_language_display() for translation in obj.translations.all()]))
    translations.short_description = 'Translations'


@admin.register(Article)
class ArticleAdmin(MarkdownxModelAdmin):
    list_display = ('title', 'image_preview', 'created_at', 'translations')
    search_fields = ('title', 'body')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    inlines = [ArticleTranslationInline]
    fieldsets = (
        (None, {
            'fields': ('title', 'body'),
            'description': 'Default content (usually English). Add translations below.'
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

    def translations(self, obj):
        return ", ".join(sorted([translation.get_language_display() for translation in obj.translations.all()]))
    translations.short_description = 'Translations'


@admin.register(Recipe)
class RecipeAdmin(MarkdownxModelAdmin):
    list_display = ('title', 'image_preview', 'created_at', 'translations')
    search_fields = ('title', 'directions', 'ingredients')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    inlines = [RecipeTranslationInline]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'time_required', 'serves', 'directions', 'ingredients'),
            'description': 'Default content (usually English). Add translations below.'
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

    def translations(self, obj):
        return ", ".join(sorted([translation.get_language_display() for translation in obj.translations.all()]))
    translations.short_description = 'Translations'


# Register translation models as standalone admin for easier management
@admin.register(VideoTranslation)
class VideoTranslationAdmin(admin.ModelAdmin):
    list_display = ('video', 'language', 'title', 'created_at')
    list_filter = ('language', 'created_at')
    search_fields = ('video__title', 'title', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ArticleTranslation)
class ArticleTranslationAdmin(MarkdownxModelAdmin):
    list_display = ('article', 'language', 'title', 'created_at')
    list_filter = ('language', 'created_at')
    search_fields = ('article__title', 'title', 'body')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(RecipeTranslation)
class RecipeTranslationAdmin(MarkdownxModelAdmin):
    list_display = ('recipe', 'language', 'title', 'created_at')
    list_filter = ('language', 'created_at')
    search_fields = ('recipe__title', 'title', 'ingredients', 'directions')
    readonly_fields = ('created_at', 'updated_at')
