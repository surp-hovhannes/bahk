from rest_framework import serializers
from django.utils import timezone
from hub.mixins import ThumbnailCacheMixin
from .models import Article, Recipe, Video, VideoTranslation, ArticleTranslation, RecipeTranslation


class VideoSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_small_url = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            'id', 'title', 'description', 'category', 'thumbnail', 
            'thumbnail_small_url', 'video', 'created_at', 
            'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_thumbnail_small_url(self, obj):
        if obj.thumbnail:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'thumbnail', 'thumbnail_small')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.thumbnail_small.url
            except:
                return None
        return None

    def get_title(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.title if translation else obj.title

    def get_description(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.description if translation else obj.description


class ArticleSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_url = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'body', 'image', 
            'thumbnail_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_thumbnail_url(self, obj):
        if obj.image:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.thumbnail.url
            except:
                return None
        return None 

    def get_title(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.title if translation else obj.title

    def get_body(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.body if translation else obj.body
    
    
class RecipeSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_url = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    time_required = serializers.SerializerMethodField()
    serves = serializers.SerializerMethodField()
    ingredients = serializers.SerializerMethodField()
    directions = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = [
            'id', 'title', 'description', 'image', 'thumbnail_url', 'created_at', 'updated_at',
            'time_required', 'serves', 'ingredients', 'directions',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_thumbnail_url(self, obj):
        if obj.image:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.thumbnail.url
            except:
                return None
        return None

    def get_title(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.title if translation else obj.title

    def get_description(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.description if translation else obj.description

    def get_time_required(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.time_required if translation else obj.time_required

    def get_serves(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.serves if translation else obj.serves

    def get_ingredients(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.ingredients if translation else obj.ingredients

    def get_directions(self, obj):
        language = self.context.get('language', 'en')
        translation = obj.translations.filter(language_code=language).first()
        return translation.directions if translation else obj.directions
