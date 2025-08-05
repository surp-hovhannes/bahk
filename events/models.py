"""Models for user events tracking."""

import json
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

User = get_user_model()


class EventType(models.Model):
    """
    Defines the types of events that can be tracked.
    This allows for flexible event categorization and easy addition of new event types.
    """
    
    # Event type constants
    USER_JOINED_FAST = 'user_joined_fast'
    USER_LEFT_FAST = 'user_left_fast'
    FAST_BEGINNING = 'fast_beginning'
    FAST_ENDING = 'fast_ending'
    DEVOTIONAL_AVAILABLE = 'devotional_available'
    FAST_PARTICIPANT_MILESTONE = 'fast_participant_milestone'
    USER_MILESTONE_REACHED = 'user_milestone_reached'  # Future feature
    USER_LOGGED_IN = 'user_logged_in'
    USER_LOGGED_OUT = 'user_logged_out'
    FAST_CREATED = 'fast_created'
    FAST_UPDATED = 'fast_updated'
    
    CORE_EVENT_TYPES = [
        (USER_JOINED_FAST, 'User Joined Fast'),
        (USER_LEFT_FAST, 'User Left Fast'),
        (FAST_BEGINNING, 'Fast Beginning'),
        (FAST_ENDING, 'Fast Ending'),
        (DEVOTIONAL_AVAILABLE, 'Devotional Available'),
        (FAST_PARTICIPANT_MILESTONE, 'Fast Participant Milestone'),
        (USER_MILESTONE_REACHED, 'User Milestone Reached'),
        (USER_LOGGED_IN, 'User Logged In'),
        (USER_LOGGED_OUT, 'User Logged Out'),
        (FAST_CREATED, 'Fast Created'),
        (FAST_UPDATED, 'Fast Updated'),
    ]
    
    # Core fields
    code = models.CharField(
        max_length=100, 
        unique=True, 
        help_text="Unique identifier for the event type (e.g., 'user_joined_fast')"
    )
    name = models.CharField(
        max_length=200, 
        help_text="Human-readable name for the event type"
    )
    description = models.TextField(
        blank=True,
        help_text="Detailed description of what this event represents"
    )
    
    # Categorization
    category = models.CharField(
        max_length=50,
        choices=[
            ('user_action', 'User Action'),
            ('system_event', 'System Event'),
            ('milestone', 'Milestone'),
            ('notification', 'Notification'),
            ('analytics', 'Analytics'),
        ],
        default='user_action',
        help_text="Category for grouping similar event types"
    )
    
    # Configuration
    is_active = models.BooleanField(
        default=True, 
        help_text="Whether this event type is currently active"
    )
    track_in_analytics = models.BooleanField(
        default=True,
        help_text="Whether to include this event type in analytics dashboards"
    )
    requires_target = models.BooleanField(
        default=False,
        help_text="Whether this event type requires a target object"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['category', 'name']
        verbose_name = 'Event Type'
        verbose_name_plural = 'Event Types'
    
    def __str__(self):
        return f"{self.name} ({self.code})"
    
    @classmethod
    def get_or_create_default_types(cls):
        """Create default event types if they don't exist."""
        created_types = []
        for code, name in cls.CORE_EVENT_TYPES:
            event_type, created = cls.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'category': cls._get_category_for_code(code),
                    'requires_target': cls._requires_target_for_code(code),
                }
            )
            if created:
                created_types.append(event_type)
        return created_types
    
    @classmethod
    def _get_category_for_code(cls, code):
        """Get appropriate category for event type code."""
        if code in [cls.USER_JOINED_FAST, cls.USER_LEFT_FAST, cls.USER_LOGGED_IN, cls.USER_LOGGED_OUT]:
            return 'user_action'
        elif code in [cls.FAST_BEGINNING, cls.FAST_ENDING, cls.FAST_CREATED, cls.FAST_UPDATED]:
            return 'system_event'
        elif code in [cls.FAST_PARTICIPANT_MILESTONE, cls.USER_MILESTONE_REACHED]:
            return 'milestone'
        elif code in [cls.DEVOTIONAL_AVAILABLE]:
            return 'notification'
        return 'user_action'
    
    @classmethod
    def _requires_target_for_code(cls, code):
        """Check if event type requires a target object."""
        return code in [
            cls.USER_JOINED_FAST, cls.USER_LEFT_FAST, cls.FAST_BEGINNING, 
            cls.FAST_ENDING, cls.DEVOTIONAL_AVAILABLE, cls.FAST_PARTICIPANT_MILESTONE,
            cls.FAST_CREATED, cls.FAST_UPDATED
        ]


class Event(models.Model):
    """
    Tracks individual user events within the application.
    Uses a flexible design to accommodate various event types and data.
    """
    
    # Core event information
    event_type = models.ForeignKey(
        EventType, 
        on_delete=models.CASCADE,
        related_name='events',
        help_text="The type of event that occurred"
    )
    
    # User who triggered the event (nullable for system events)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='events',
        help_text="User who triggered this event (null for system events)"
    )
    
    # Generic foreign key for target object (Fast, Profile, etc.)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Content type of the target object"
    )
    object_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="ID of the target object"
    )
    target = GenericForeignKey('content_type', 'object_id')
    
    # Event details
    title = models.CharField(
        max_length=255,
        help_text="Brief title describing the event"
    )
    description = models.TextField(
        blank=True,
        help_text="Detailed description of the event"
    )
    
    # Flexible data storage for event-specific information
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional data related to the event (JSON format)"
    )
    
    # Metadata
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="When the event occurred"
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address from which the event originated"
    )
    user_agent = models.TextField(
        blank=True,
        help_text="User agent string from the request"
    )
    
    # System metadata
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['event_type', '-timestamp']),
            models.Index(fields=['content_type', 'object_id', '-timestamp']),
            models.Index(fields=['-timestamp']),
        ]
        verbose_name = 'Event'
        verbose_name_plural = 'Events'
    
    def __str__(self):
        user_str = f"{self.user}" if self.user else "System"
        target_str = f" → {self.target}" if self.target else ""
        return f"{user_str}: {self.title}{target_str} ({self.timestamp.strftime('%Y-%m-%d %H:%M')})"
    
    def clean(self):
        """Validate the event data."""
        super().clean()
        
        # Validate that required target is provided
        if self.event_type and self.event_type.requires_target:
            if not self.target:
                raise ValidationError(
                    f"Event type '{self.event_type.code}' requires a target object."
                )
        
        # Validate JSON data
        if self.data and not isinstance(self.data, dict):
            raise ValidationError("Event data must be a valid JSON object.")
    
    def save(self, *args, **kwargs):
        """Override save to perform validation."""
        self.full_clean()
        super().save(*args, **kwargs)
    
    @property
    def target_model_name(self):
        """Get the model name of the target object."""
        if self.content_type:
            return self.content_type.model
        return None
    
    @property
    def age_in_hours(self):
        """Get the age of the event in hours."""
        return (timezone.now() - self.timestamp).total_seconds() / 3600
    
    @property
    def formatted_data(self):
        """Get formatted JSON data for display."""
        if self.data:
            return json.dumps(self.data, indent=2)
        return "{}"
    
    @classmethod
    def create_event(cls, event_type_code, user=None, target=None, title=None, 
                    description="", data=None, request=None):
        """
        Convenience method to create an event.
        
        Args:
            event_type_code: String code for the event type
            user: User who triggered the event
            target: Target object (Fast, Profile, etc.)
            title: Event title (auto-generated if not provided)
            description: Event description
            data: Additional event data (dict)
            request: HTTP request object (for IP/user agent)
        
        Returns:
            Event instance
        """
        try:
            event_type = EventType.objects.get(code=event_type_code, is_active=True)
        except EventType.DoesNotExist:
            raise ValueError(f"Event type '{event_type_code}' does not exist or is inactive.")
        
        # Auto-generate title if not provided
        if not title:
            title = cls._generate_title(event_type, user, target)
        
        # Extract request metadata
        ip_address = None
        user_agent = ""
        if request:
            ip_address = cls._get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        # Create the event
        event = cls.objects.create(
            event_type=event_type,
            user=user,
            target=target,
            title=title,
            description=description,
            data=data or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        
        return event
    
    @classmethod
    def _generate_title(cls, event_type, user, target):
        """Generate a default title for the event."""
        user_str = str(user) if user else "System"
        
        if event_type.code == EventType.USER_JOINED_FAST:
            return f"{user_str} joined {target}"
        elif event_type.code == EventType.USER_LEFT_FAST:
            return f"{user_str} left {target}"
        elif event_type.code == EventType.FAST_BEGINNING:
            return f"{target} has begun"
        elif event_type.code == EventType.FAST_ENDING:
            return f"{target} has ended"
        elif event_type.code == EventType.DEVOTIONAL_AVAILABLE:
            return f"New devotional available for {target}"
        elif event_type.code == EventType.FAST_PARTICIPANT_MILESTONE:
            return f"{target} reached participation milestone"
        else:
            return f"{user_str} triggered {event_type.name.lower()}"
    
    @classmethod
    def _get_client_ip(cls, request):
        """Extract client IP address from request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
