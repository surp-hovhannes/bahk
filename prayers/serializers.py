"""Serializers for prayers app."""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import activate

from hub.mixins import ThumbnailCacheMixin
from learning_resources.serializers import BookmarkOptimizedSerializerMixin
from prayers.models import (
    Prayer,
    PrayerSet,
    PrayerSetMembership,
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
)

User = get_user_model()


class PrayerSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer):
    """Serializer for Prayer model."""
    
    tags = serializers.SerializerMethodField()
    church_name = serializers.CharField(source='church.name', read_only=True)
    fast_name = serializers.CharField(source='fast.name', read_only=True)
    is_bookmarked = serializers.SerializerMethodField()
    
    class Meta:
        model = Prayer
        fields = [
            'id', 'title', 'text', 'category', 'church', 'church_name',
            'fast', 'fast_name', 'tags', 'created_at', 'updated_at', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']
    
    def get_tags(self, obj):
        """Return list of tag names."""
        return [tag.name for tag in obj.tags.all()]
    
    def to_representation(self, instance):
        """Add translation support."""
        lang = self.context.get('lang') or (
            self.context.get('request').query_params.get('lang') 
            if self.context.get('request') else None
        ) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        data['text'] = getattr(instance, 'text_i18n', instance.text)
        return data


class PrayerSetMembershipSerializer(serializers.ModelSerializer):
    """Serializer for PrayerSetMembership model."""
    
    prayer = PrayerSerializer(read_only=True)
    
    class Meta:
        model = PrayerSetMembership
        fields = ['id', 'prayer', 'order']


class PrayerSetSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    """Serializer for PrayerSet model."""
    
    church_name = serializers.CharField(source='church.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    prayers = serializers.SerializerMethodField()
    prayer_count = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    
    class Meta:
        model = PrayerSet
        fields = [
            'id', 'title', 'description', 'category', 'church', 'church_name',
            'image', 'thumbnail_url', 'prayers', 'prayer_count',
            'created_at', 'updated_at', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']
    
    def get_thumbnail_url(self, obj):
        """Get cached or generated thumbnail URL."""
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
    
    def get_prayers(self, obj):
        """Get ordered list of prayers in this set."""
        memberships = obj.memberships.select_related('prayer').order_by('order')
        serializer = PrayerSetMembershipSerializer(
            memberships,
            many=True,
            context=self.context
        )
        return serializer.data
    
    def get_prayer_count(self, obj):
        """Return the number of prayers in this set."""
        return obj.prayers.count()
    
    def to_representation(self, instance):
        """Add translation support."""
        lang = self.context.get('lang') or (
            self.context.get('request').query_params.get('lang')
            if self.context.get('request') else None
        ) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        data['description'] = getattr(instance, 'description_i18n', instance.description)
        return data


class PrayerSetListSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    """Lightweight serializer for listing prayer sets (without full prayer details)."""
    
    church_name = serializers.CharField(source='church.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    prayer_count = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    
    class Meta:
        model = PrayerSet
        fields = [
            'id', 'title', 'description', 'category', 'church', 'church_name',
            'thumbnail_url', 'prayer_count', 'created_at', 'updated_at', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']
    
    def get_thumbnail_url(self, obj):
        """Get cached or generated thumbnail URL."""
        if obj.image:
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url
            try:
                return obj.thumbnail.url
            except (AttributeError, ValueError, OSError):
                return None
        return None
    
    def get_prayer_count(self, obj):
        """Return the number of prayers in this set."""
        return obj.prayers.count()
    
    def to_representation(self, instance):
        """Add translation support."""
        lang = self.context.get('lang') or (
            self.context.get('request').query_params.get('lang')
            if self.context.get('request') else None
        ) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        data['description'] = getattr(instance, 'description_i18n', instance.description)
        return data



# Prayer Request Serializers


class RequesterSerializer(serializers.ModelSerializer):
    """Minimal serializer for prayer request requester."""

    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name']
        read_only_fields = fields

    def get_full_name(self, obj):
        """Get user's profile name or email."""
        if hasattr(obj, 'profile') and obj.profile and obj.profile.name:
            return obj.profile.name
        return obj.email


class PrayerRequestSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    """Serializer for PrayerRequest model."""

    requester = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    acceptance_count = serializers.SerializerMethodField()
    prayer_log_count = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    has_accepted = serializers.SerializerMethodField()
    has_prayed_today = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = PrayerRequest
        fields = [
            'id', 'title', 'description', 'is_anonymous', 'duration_days',
            'expiration_date', 'image', 'thumbnail_url', 'reviewed', 'status',
            'requester', 'created_at', 'updated_at', 'acceptance_count',
            'prayer_log_count', 'is_expired', 'has_accepted', 'has_prayed_today',
            'is_owner'
        ]
        read_only_fields = [
            'expiration_date', 'reviewed', 'status', 'created_at', 'updated_at',
            'acceptance_count', 'prayer_log_count', 'is_expired'
        ]

    def get_requester(self, obj):
        """Return requester info, hiding identity if anonymous (except for owner/staff)."""
        request = self.context.get('request')

        # Show requester to staff and the requester themselves
        if request and (request.user.is_staff or request.user == obj.requester):
            return RequesterSerializer(obj.requester).data

        # Hide requester for anonymous requests
        if obj.is_anonymous:
            return None

        return RequesterSerializer(obj.requester).data

    def get_thumbnail_url(self, obj):
        """Get cached or generated thumbnail URL."""
        if obj.image:
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url

            try:
                return obj.thumbnail.url
            except (AttributeError, ValueError, OSError):
                return None
        return None

    def get_acceptance_count(self, obj):
        """Get the number of users who accepted this request."""
        return obj.get_acceptance_count()

    def get_prayer_log_count(self, obj):
        """Get the total number of prayer logs."""
        return obj.get_prayer_log_count()

    def get_is_expired(self, obj):
        """Check if the prayer request has expired."""
        return obj.is_expired()

    def get_has_accepted(self, obj):
        """Check if current user has accepted this request."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.acceptances.filter(user=request.user).exists()
        return False

    def get_has_prayed_today(self, obj):
        """Check if current user has prayed for this request today."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            today = timezone.now().date()
            return obj.prayer_logs.filter(
                user=request.user,
                prayed_on_date=today
            ).exists()
        return False

    def get_is_owner(self, obj):
        """Check if current user is the owner of this prayer request."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return request.user == obj.requester
        return False


class PrayerRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating prayer requests."""

    class Meta:
        model = PrayerRequest
        fields = [
            'title', 'description', 'is_anonymous', 'duration_days', 'image'
        ]

    def validate(self, data):
        """Validate that user doesn't exceed max active requests."""
        request = self.context.get('request')
        if request and request.user:
            active_count = PrayerRequest.objects.filter(
                requester=request.user,
                status__in=['pending_moderation', 'approved']
            ).exclude(
                expiration_date__lt=timezone.now()
            ).count()

            if active_count >= PrayerRequest.MAX_ACTIVE_REQUESTS_PER_USER:
                raise serializers.ValidationError(
                    f'You cannot have more than {PrayerRequest.MAX_ACTIVE_REQUESTS_PER_USER} '
                    f'active prayer requests at once.'
                )

        return data

    def create(self, validated_data):
        """Create prayer request with requester from request."""
        request = self.context.get('request')
        validated_data['requester'] = request.user
        return super().create(validated_data)


class PrayerRequestUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating prayer requests (only when pending moderation)."""

    class Meta:
        model = PrayerRequest
        fields = ['title', 'description', 'image']

    def validate(self, data):
        """Only allow updates if status is pending_moderation."""
        instance = self.instance
        if instance and instance.status != 'pending_moderation':
            raise serializers.ValidationError(
                'Prayer requests can only be edited while pending moderation.'
            )
        return data


class PrayerRequestAcceptanceSerializer(serializers.ModelSerializer):
    """Serializer for PrayerRequestAcceptance model."""

    prayer_request = PrayerRequestSerializer(read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = PrayerRequestAcceptance
        fields = ['id', 'prayer_request', 'user', 'user_email', 'accepted_at']
        read_only_fields = ['user', 'accepted_at']


class PrayerRequestPrayerLogSerializer(serializers.ModelSerializer):
    """Serializer for PrayerRequestPrayerLog model."""

    prayer_request = PrayerRequestSerializer(read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = PrayerRequestPrayerLog
        fields = ['id', 'prayer_request', 'user', 'user_email', 'prayed_on_date', 'created_at']
        read_only_fields = ['user', 'created_at']


class PrayerRequestThanksSerializer(serializers.Serializer):
    """Serializer for sending thanks message after prayer request completion."""

    message = serializers.CharField(
        max_length=500,
        required=True,
        help_text='Thank you message to send to all who accepted the prayer request'
    )

    def validate_message(self, value):
        """Validate message is not empty."""
        if not value.strip():
            raise serializers.ValidationError('Message cannot be empty.')
        return value.strip()
