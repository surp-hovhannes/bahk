from rest_framework import serializers
from .models import Video, Article

class VideoSerializer(serializers.ModelSerializer):
    thumbnail_small_url = serializers.URLField(source='thumbnail_small.url', read_only=True)

    class Meta:
        model = Video
        fields = [
            'id', 'title', 'description', 'thumbnail', 
            'thumbnail_small_url', 'video', 'created_at', 
            'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

class ArticleSerializer(serializers.ModelSerializer):
    thumbnail_url = serializers.URLField(source='thumbnail.url', read_only=True)

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'body', 'image', 
            'thumbnail_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at'] 