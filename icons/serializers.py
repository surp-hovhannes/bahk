"""Serializers for the icons app."""
from rest_framework import serializers
from icons.models import Icon


class IconSerializer(serializers.ModelSerializer):
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
        """Get the thumbnail URL, never triggering generation during serialization.

        Falls back to cached thumbnail URL if available, or returns None if not cached.
        Thumbnail generation is deferred to background tasks to avoid OOM during requests.
        """
        if obj.cached_thumbnail_url:
            return obj.cached_thumbnail_url
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


class IconFeedbackSerializer(serializers.Serializer):
    """Write-only serializer for icon feedback submissions."""

    feedback_type = serializers.ChoiceField(
        choices=['mislabel', 'suggested_tags', 'general']
    )
    description = serializers.CharField(min_length=10, max_length=2000)
    suggested_tags = serializers.CharField(
        required=False, allow_blank=True, default=''
    )
    submitter_email = serializers.EmailField(
        required=False, allow_blank=True, default=''
    )

    def validate(self, attrs):
        """If feedback_type is 'suggested_tags', require suggested_tags."""
        if attrs.get('feedback_type') == 'suggested_tags' and not attrs.get('suggested_tags', '').strip():
            raise serializers.ValidationError({
                'suggested_tags': 'Suggested tags are required when feedback type is "Suggest Tags".'
            })
        return attrs
