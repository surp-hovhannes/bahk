"""Serializers for the icons app."""
from rest_framework import serializers
from hub.mixins import ThumbnailCacheMixin
from icons.models import Icon


class IconSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    """Serializer for Icon model."""
    
    church_name = serializers.CharField(source='church.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    tag_list = serializers.SerializerMethodField()
    
    class Meta:
        model = Icon
        fields = [
            'id', 'title', 'church', 'church_name', 'tag_list',
            'image', 'thumbnail_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_thumbnail_url(self, obj):
        """Get the thumbnail URL with caching support."""
        if obj.image:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.thumbnail.url
            except (AttributeError, ValueError, OSError):
                return None
        return None
    
    def get_tag_list(self, obj):
        """Get list of tags as strings."""
        return [tag.name for tag in obj.tags.all()]
