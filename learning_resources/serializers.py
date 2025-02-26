from rest_framework import serializers
from .models import Video, Article

class VideoSerializer(serializers.ModelSerializer):
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
        if obj.thumbnail_small:
            return obj.thumbnail_small.url
        return None

class ArticleSerializer(serializers.ModelSerializer):
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'body', 'image', 
            'thumbnail_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_thumbnail_url(self, obj):
        if obj.thumbnail:
            return obj.thumbnail.url
        return None 