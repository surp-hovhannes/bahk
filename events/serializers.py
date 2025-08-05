"""
Django REST Framework serializers for the events app.
"""

from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType
from .models import Event, EventType


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
    
    milestone_events = serializers.ListField(
        child=EventListSerializer(),
        help_text="Recent milestone events"
    )


class UserEventStatsSerializer(serializers.Serializer):
    """Serializer for user-specific event statistics."""
    
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    
    total_events = serializers.IntegerField()
    fasts_joined = serializers.IntegerField()
    fasts_left = serializers.IntegerField()
    net_fast_joins = serializers.IntegerField()
    
    recent_events = serializers.ListField(
        child=EventListSerializer(),
        help_text="Recent events for this user"
    )
    
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
    
    milestone_events = serializers.ListField(
        child=EventListSerializer(),
        help_text="Milestone events for this fast"
    )
    
    join_timeline = serializers.DictField(
        help_text="Timeline of joins and leaves"
    )
    
    recent_activity = serializers.ListField(
        child=EventListSerializer(),
        help_text="Recent activity for this fast"
    )