"""Serializers for prayers app."""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import activate

from icons.models import Icon
from hub.mixins import ThumbnailCacheMixin
from learning_resources.serializers import BookmarkOptimizedSerializerMixin
from prayers.models import (
    Prayer,
    PrayerSet,
    PrayerSetMembership,
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
    FeastPrayer,
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
            'id', 'title', 'text', 'category', 'video', 'church', 'church_name',
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
    profile_image_url = serializers.SerializerMethodField()
    profile_image_thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'profile_image_url', 'profile_image_thumbnail_url']
        read_only_fields = fields

    def get_full_name(self, obj):
        """Get user's profile name or email."""
        if hasattr(obj, 'profile') and obj.profile and obj.profile.name:
            return obj.profile.name
        return obj.email

    def get_profile_image_url(self, obj):
        """Get the original profile image URL."""
        if hasattr(obj, 'profile') and obj.profile and obj.profile.profile_image:
            try:
                return obj.profile.profile_image.url
            except (AttributeError, ValueError, OSError):
                return None
        return None

    def get_profile_image_thumbnail_url(self, obj):
        """Get the cached or generated thumbnail URL for profile image."""
        if hasattr(obj, 'profile') and obj.profile and obj.profile.profile_image:
            # Try to use cached URL if available and recent
            if obj.profile.cached_thumbnail_url:
                return obj.profile.cached_thumbnail_url

            # Fall back to generating thumbnail URL
            try:
                return obj.profile.profile_image_thumbnail.url
            except (AttributeError, ValueError, OSError):
                return None
        return None


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
            'expiration_date', 'image', 'icon_id', 'thumbnail_url', 'reviewed', 'status',
            'moderation_severity', 'requester', 'created_at', 'updated_at',
            'acceptance_count', 'prayer_log_count', 'is_expired', 'has_accepted',
            'has_prayed_today', 'is_owner'
        ]
        read_only_fields = [
            'expiration_date', 'reviewed', 'status', 'moderation_severity',
            'created_at', 'updated_at', 'acceptance_count', 'prayer_log_count',
            'is_expired'
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
        """Get cached or generated thumbnail URL (uploaded image, or fallback icon)."""
        if obj.image:
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url

            try:
                return obj.thumbnail.url
            except (AttributeError, ValueError, OSError):
                return None
        if obj.icon:
            cached_url = self.update_thumbnail_cache(obj.icon, 'image', 'thumbnail')
            if cached_url:
                return cached_url
            try:
                return obj.icon.thumbnail.url
            except (AttributeError, ValueError, OSError):
                # As a last resort, try full image URL
                try:
                    return obj.icon.image.url
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

    icon_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = PrayerRequest
        fields = [
            'title', 'description', 'is_anonymous', 'duration_days', 'image', 'icon_id'
        ]

    def validate_icon_id(self, value):
        """Validate icon selection against requester's Profile.church (when set)."""
        if value is None:
            return value

        try:
            icon = Icon.objects.select_related('church').get(id=value)
        except Icon.DoesNotExist:
            raise serializers.ValidationError('Invalid icon_id.')

        request = self.context.get('request')
        user = getattr(request, 'user', None)
        profile = getattr(user, 'profile', None) if user else None
        profile_church = getattr(profile, 'church', None) if profile else None

        if profile_church and icon.church_id != profile_church.id:
            raise serializers.ValidationError('Icon must belong to your church.')

        return value

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
        icon_id = validated_data.pop('icon_id', None)
        validated_data['requester'] = request.user
        if icon_id is not None:
            validated_data['icon_id'] = icon_id
        return super().create(validated_data)


class PrayerRequestUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating prayer requests (only when pending moderation)."""

    icon_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = PrayerRequest
        fields = ['title', 'description', 'image', 'icon_id']

    def validate_icon_id(self, value):
        """Validate icon selection against requester's Profile.church (when set)."""
        if value is None:
            return value

        try:
            icon = Icon.objects.select_related('church').get(id=value)
        except Icon.DoesNotExist:
            raise serializers.ValidationError('Invalid icon_id.')

        request = self.context.get('request')
        user = getattr(request, 'user', None)
        profile = getattr(user, 'profile', None) if user else None
        profile_church = getattr(profile, 'church', None) if profile else None

        if profile_church and icon.church_id != profile_church.id:
            raise serializers.ValidationError('Icon must belong to your church.')

        return value

    def validate(self, data):
        """Only allow updates if status is pending_moderation."""
        instance = self.instance
        if instance and instance.status != 'pending_moderation':
            raise serializers.ValidationError(
                'Prayer requests can only be edited while pending moderation.'
            )
        return data

    def update(self, instance, validated_data):
        """Support updating icon by icon_id."""
        icon_id = validated_data.pop('icon_id', serializers.empty)
        if icon_id is not serializers.empty:
            instance.icon_id = icon_id
        return super().update(instance, validated_data)


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


class FeastPrayerSerializer(serializers.ModelSerializer):
    """Serializer for FeastPrayer with translation and rendering support."""

    title_template = serializers.CharField(source='title', read_only=True)
    text_template = serializers.CharField(source='text', read_only=True)
    title_rendered = serializers.SerializerMethodField()
    text_rendered = serializers.SerializerMethodField()

    class Meta:
        model = FeastPrayer
        fields = [
            'id', 'designation', 'title_template', 'text_template',
            'title_rendered', 'text_rendered', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_title_rendered(self, obj):
        """Return empty string - populated in to_representation."""
        return ""

    def get_text_rendered(self, obj):
        """Return empty string - populated in to_representation."""
        return ""

    def to_representation(self, instance):
        """Add translation support and template rendering."""
        # Get language from request
        lang = self.context.get('lang') or (
            self.context.get('request').query_params.get('lang')
            if self.context.get('request') else None
        ) or 'en'
        activate(lang)

        data = super().to_representation(instance)

        # Get translated template fields
        data['title_template'] = getattr(instance, 'title_i18n', instance.title)
        data['text_template'] = getattr(instance, 'text_i18n', instance.text)

        # Render with feast if provided in context
        feast = self.context.get('feast')
        if feast:
            rendered = instance.render_for_feast(feast, lang)
            data['title_rendered'] = rendered['title']
            data['text_rendered'] = rendered['text']

        return data
