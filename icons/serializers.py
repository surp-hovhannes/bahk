"""Serializers for the icons app."""
from rest_framework import serializers
from hub.mixins import ThumbnailCacheMixin
from icons.models import Icon


class IconSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    """Serializer for Icon model."""

    church_name = serializers.CharField(source='church.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    tag_list = serializers.SerializerMethodField()
    # Writable tags field for POST requests (comma-separated string)
    tags = serializers.CharField(required=False, write_only=True, allow_blank=True)

    class Meta:
        model = Icon
        fields = [
            'id', 'title', 'church', 'church_name', 'tags', 'tag_list',
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

    def create(self, validated_data):
        """Handle tag creation on icon upload."""
        # Extract tags from validated data (comes as comma-separated string)
        tags_string = validated_data.pop('tags', '')
        icon = Icon.objects.create(**validated_data)

        # Process tags: split by comma and clean up
        if tags_string:
            tag_list = [tag.strip() for tag in tags_string.split(',') if tag.strip()]
            if tag_list:
                icon.tags.set(tag_list)

        return icon

    def update(self, instance, validated_data):
        """Handle tag updates on icon update."""
        tags_string = validated_data.pop('tags', None)

        # Update all other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update tags if provided
        if tags_string is not None:
            tag_list = [tag.strip() for tag in tags_string.split(',') if tag.strip()]
            if tag_list:
                instance.tags.set(tag_list)
            else:
                instance.tags.clear()

        return instance
