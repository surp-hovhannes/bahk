"""
Django REST Framework serializers for the events app.
"""

from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType
from .models import Event, EventType
from .models import UserActivityFeed
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.html import strip_tags
import re
from hub.mixins import ThumbnailCacheMixin


class EventTypeSerializer(serializers.ModelSerializer):
    """Serializer for EventType model."""
    
    event_count = serializers.SerializerMethodField()
    
    class Meta:
        model = EventType
        fields = [
            'id', 'code', 'name', 'description', 'category',
            'is_active', 'track_in_analytics', 'requires_target',
            'event_count', 'created_at', 'updated_at'
        ]
    
    def get_event_count(self, obj):
        """Get the total number of events for this type."""
        return obj.events.count()


class EventSerializer(serializers.ModelSerializer):
    """Serializer for Event model."""
    
    event_type_name = serializers.CharField(source='event_type.name', read_only=True)
    event_type_code = serializers.CharField(source='event_type.code', read_only=True)
    event_type_category = serializers.CharField(source='event_type.category', read_only=True)
    
    user_username = serializers.CharField(source='user.username', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    target_type = serializers.SerializerMethodField()
    target_str = serializers.SerializerMethodField()
    
    age_hours = serializers.ReadOnlyField(source='age_in_hours')
    
    class Meta:
        model = Event
        fields = [
            'id', 'title', 'description', 'timestamp', 'data',
            'event_type_name', 'event_type_code', 'event_type_category',
            'user_username', 'user_email',
            'target_type', 'target_str', 'object_id',
            'age_hours', 'ip_address', 'created_at'
        ]
    
    def get_target_type(self, obj):
        """Get the model name of the target object."""
        if obj.content_type:
            return f"{obj.content_type.app_label}.{obj.content_type.model}"
        return None
    
    def get_target_str(self, obj):
        """Get string representation of target object."""
        if obj.target:
            return str(obj.target)[:100]  # Limit length
        return None


class EventListSerializer(EventSerializer):
    """Lighter serializer for event lists (without heavy fields)."""
    
    class Meta(EventSerializer.Meta):
        fields = [
            'id', 'title', 'timestamp',
            'event_type_name', 'event_type_code', 'event_type_category',
            'user_username', 'target_type', 'target_str', 'age_hours'
        ]


class EventStatsSerializer(serializers.Serializer):
    """Serializer for event statistics."""
    
    total_events = serializers.IntegerField()
    events_last_24h = serializers.IntegerField()
    events_last_7d = serializers.IntegerField()
    events_last_30d = serializers.IntegerField()
    
    top_event_types = serializers.ListField(
        child=serializers.DictField(), 
        help_text="Top event types by count"
    )
    
    events_by_day = serializers.DictField(
        help_text="Events count by day for the last 30 days"
    )
    
    fast_join_stats = serializers.DictField(
        help_text="Fast joining statistics"
    )
    
    milestone_events = EventListSerializer(many=True)


class UserEventStatsSerializer(serializers.Serializer):
    """Serializer for user-specific event statistics."""
    
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    
    total_events = serializers.IntegerField()
    fasts_joined = serializers.IntegerField()
    fasts_left = serializers.IntegerField()
    net_fast_joins = serializers.IntegerField()
    
    recent_events = EventListSerializer(many=True)
    
    event_types_breakdown = serializers.DictField(
        help_text="Breakdown of events by type"
    )


class FastEventStatsSerializer(serializers.Serializer):
    """Serializer for fast-specific event statistics."""
    
    fast_id = serializers.IntegerField()
    fast_name = serializers.CharField()
    
    total_events = serializers.IntegerField()
    current_participants = serializers.IntegerField()
    total_joins = serializers.IntegerField()
    total_leaves = serializers.IntegerField()
    net_joins = serializers.IntegerField()
    
    milestone_events = EventListSerializer(many=True)
    
    join_timeline = serializers.DictField(
        help_text="Timeline of joins and leaves"
    )
    
    recent_activity = EventListSerializer(many=True)


class UserActivityFeedSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    """
    Serializer for user activity feed items.
    """
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    age_display = serializers.SerializerMethodField()
    target_type = serializers.SerializerMethodField()
    target_id = serializers.SerializerMethodField()
    target_thumbnail = serializers.SerializerMethodField()
    
    class Meta:
        model = UserActivityFeed
        fields = [
            'id', 'activity_type', 'activity_type_display', 'title', 'description',
            'is_read', 'read_at', 'created_at', 'age_display', 'data',
            'target_type', 'target_id', 'target_thumbnail'
        ]
        read_only_fields = ['id', 'created_at', 'age_display']
        extra_kwargs = {
            'title': {"allow_blank": False},
        }

    def _lang(self):
        return self.context.get('lang') or get_language() or 'en'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Replace title/description with translated values per requested lang
        lang = self._lang()
        data['title'] = instance.safe_translation_getter('title', language_code=lang, any_language=True)
        data['description'] = instance.safe_translation_getter('description', language_code=lang, any_language=True)
        return data
    
    def get_age_display(self, obj):
        """Show how long ago the activity occurred."""
        age_hours = (timezone.now() - obj.created_at).total_seconds() / 3600
        if age_hours < 1:
            minutes = int(age_hours * 60)
            return f"{minutes}m ago"
        elif age_hours < 24:
            return f"{int(age_hours)}h ago"
        else:
            days = int(age_hours / 24)
            return f"{days}d ago"
    
    def get_target_type(self, obj):
        """Get the target object type."""
        if obj.content_type:
            return f"{obj.content_type.app_label}.{obj.content_type.model}"
        return None
    
    def get_target_id(self, obj):
        """Get the target object ID."""
        return obj.object_id
    
    def get_target_thumbnail(self, obj):
        """Get the thumbnail URL for the target object if available."""
        if not obj.target:
            return None
        
        target = obj.target
        
        # Handle Fast thumbnails
        if hasattr(target, 'image') and target.image:
            try:
                # Use cached thumbnail URL if available
                cached_url = self.update_thumbnail_cache(target, 'image', 'image_thumbnail')
                if cached_url:
                    return cached_url
                
                # Fall back to direct thumbnail URL
                if hasattr(target, 'image_thumbnail'):
                    return target.image_thumbnail.url
            except Exception:
                pass
        
        # Handle Profile thumbnails
        if hasattr(target, 'profile_image') and target.profile_image:
            try:
                # Use cached thumbnail URL if available
                cached_url = self.update_thumbnail_cache(target, 'profile_image', 'profile_image_thumbnail')
                if cached_url:
                    return cached_url
                
                # Fall back to direct thumbnail URL
                if hasattr(target, 'profile_image_thumbnail'):
                    return target.profile_image_thumbnail.url
            except Exception:
                pass
        
        # Handle Video/Article/Recipe thumbnails (for devotionals, learning resources)
        if hasattr(target, 'thumbnail') and target.thumbnail:
            try:
                # Use cached thumbnail URL if available (for video thumbnails)
                if hasattr(target, 'cached_thumbnail_url') and target.cached_thumbnail_url:
                    return target.cached_thumbnail_url
                
                # Fall back to direct thumbnail URL (works for both video and article/recipe thumbnails)
                return target.thumbnail.url
            except Exception:
                pass
        
        return None

    # -----------------------------
    # Validation and sanitization
    # -----------------------------

    @staticmethod
    def _sanitize_text(value: str, max_length: int) -> str:
        if value is None:
            return value
        # Remove NULLs and control characters
        value = value.replace('\x00', '')
        value = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]", "", value)
        # Strip HTML tags
        value = strip_tags(value)
        # Collapse whitespace
        value = re.sub(r"\s+", " ", value).strip()
        # Enforce length limits
        if len(value) > max_length:
            value = value[:max_length]
        return value

    def _sanitize_data_value(self, value, depth: int = 0):
        if depth > 3:
            raise serializers.ValidationError({
                'data': 'Data is too deeply nested (max depth 3).'
            })
        if isinstance(value, str):
            return self._sanitize_text(value, max_length=1000)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            if len(value) > 100:
                raise serializers.ValidationError({'data': 'Lists in data may not exceed 100 items.'})
            return [self._sanitize_data_value(v, depth + 1) for v in value]
        if isinstance(value, dict):
            if len(value) > 50:
                raise serializers.ValidationError({'data': 'Data may not contain more than 50 keys.'})
            sanitized = {}
            for k, v in value.items():
                if not isinstance(k, str):
                    raise serializers.ValidationError({'data': 'All keys in data must be strings.'})
                key_clean = self._sanitize_text(k, max_length=100)
                sanitized[key_clean] = self._sanitize_data_value(v, depth + 1)
            return sanitized
        # Fallback to string representation for unsupported types
        return self._sanitize_text(str(value), max_length=1000)

    def validate_activity_type(self, value: str) -> str:
        valid_values = {code for code, _ in UserActivityFeed.ACTIVITY_TYPES}
        if value not in valid_values:
            raise serializers.ValidationError("Invalid activity_type.")
        return value

    def validate_data(self, value):
        if value in (None, {}):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError('Data must be an object/dictionary.')
        # Sanitize recursively
        return self._sanitize_data_value(value)

    def validate(self, attrs):
        # Ensure read_at only present when is_read is True
        is_read = attrs.get('is_read')
        read_at = attrs.get('read_at')
        instance_is_read = getattr(self.instance, 'is_read', False) if getattr(self, 'instance', None) else False
        effective_is_read = is_read if is_read is not None else instance_is_read
        if read_at and not effective_is_read:
            raise serializers.ValidationError({'read_at': 'read_at can only be set when is_read is true.'})
        return attrs

    def create(self, validated_data):
        # Sanitize text fields
        if 'title' in validated_data:
            validated_data['title'] = self._sanitize_text(validated_data['title'], max_length=255)
        if 'description' in validated_data and validated_data['description'] is not None:
            validated_data['description'] = self._sanitize_text(validated_data['description'], max_length=5000)
        # Sanitize data (validate_data already handles)
        if 'data' in validated_data and validated_data['data']:
            validated_data['data'] = self._sanitize_data_value(validated_data['data'])
        # Auto-set read_at when marking as read
        if validated_data.get('is_read') and not validated_data.get('read_at'):
            validated_data['read_at'] = timezone.now()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Sanitize text fields
        if 'title' in validated_data:
            validated_data['title'] = self._sanitize_text(validated_data['title'], max_length=255)
        if 'description' in validated_data and validated_data['description'] is not None:
            validated_data['description'] = self._sanitize_text(validated_data['description'], max_length=5000)
        # Sanitize data
        if 'data' in validated_data and validated_data['data'] is not None:
            validated_data['data'] = self._sanitize_data_value(validated_data['data'])
        # Auto-set/clear read_at based on is_read
        if 'is_read' in validated_data:
            if validated_data['is_read'] and not validated_data.get('read_at'):
                validated_data['read_at'] = timezone.now()
            if not validated_data['is_read']:
                validated_data['read_at'] = None
        return super().update(instance, validated_data)


class UserActivityFeedSummarySerializer(serializers.Serializer):
    """
    Serializer for activity feed summary statistics.
    """
    total_items = serializers.IntegerField()
    unread_count = serializers.IntegerField()
    read_count = serializers.IntegerField()
    activity_types = serializers.DictField()
    recent_activity = serializers.ListField(child=UserActivityFeedSerializer())