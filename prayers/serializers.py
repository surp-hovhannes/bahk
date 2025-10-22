"""Serializers for prayers app."""
from rest_framework import serializers
from django.utils.translation import activate

from hub.mixins import ThumbnailCacheMixin
from learning_resources.serializers import BookmarkOptimizedSerializerMixin
from prayers.models import Prayer, PrayerSet, PrayerSetMembership


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

