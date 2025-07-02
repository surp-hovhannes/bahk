from rest_framework import serializers
from django.utils import timezone
from hub.mixins import ThumbnailCacheMixin
from .models import Article, Recipe, Video
from hub.models import DevotionalSet


class VideoSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_small_url = serializers.SerializerMethodField()

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

class ArticleSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_url = serializers.SerializerMethodField()

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
    
class RecipeSerializer(ArticleSerializer):
    class Meta:
        model = Recipe
        fields = [
            'id', 'title', 'description', 'image', 'thumbnail_url', 'created_at', 'updated_at',
            'time_required', 'serves', 'ingredients', 'directions',
        ]
        read_only_fields = ['created_at', 'updated_at']


class DevotionalSetSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    fast_name = serializers.CharField(source='fast.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    number_of_days = serializers.ReadOnlyField()
    
    class Meta:
        model = DevotionalSet
        fields = [
            'id', 'title', 'description', 'fast', 'fast_name',
            'image', 'thumbnail_url', 'number_of_days',
            'created_at', 'updated_at'
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
