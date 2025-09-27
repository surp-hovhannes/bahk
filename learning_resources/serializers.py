from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.utils.translation import get_language
from hub.mixins import ThumbnailCacheMixin
from .models import Article, Recipe, Video, Bookmark
from .cache import BookmarkCacheManager
from hub.models import DevotionalSet


class BookmarkOptimizedSerializerMixin:
    """
    Mixin for serializers to provide Redis-cached bookmark checking.
    
    Uses explicit context data for bookmark cache to optimize performance while
    avoiding memory issues and improving testability. Falls back to Redis cache
    manager and database queries when needed.
    
    Expected context keys:
        - bookmark_cache_data: Pre-computed bookmark status dict {object_id: bool}
        - use_bookmark_cache: Boolean flag to enable Redis fallback
        - request: Django request object with authenticated user
    """
    
    def get_is_bookmarked(self, obj):
        """Check if the current user has bookmarked this item (Redis optimized)."""
        request = self.context.get('request')
        if not (request and request.user.is_authenticated):
            return False
        
        # Try explicit cache data from context first (fastest path)
        bookmark_cache_data = self.context.get('bookmark_cache_data')
        if bookmark_cache_data is not None:
            return bookmark_cache_data.get(obj.id, False)
        
        # Fallback to Redis cache manager (still very fast)
        if self.context.get('use_bookmark_cache', False):
            return BookmarkCacheManager.is_bookmarked(request.user, obj)
        
        # Final fallback to individual database query (rare case)
        return Bookmark.objects.filter(
            user=request.user,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.id
        ).exists()


class VideoSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_small_url = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            'id', 'title', 'description', 'category', 'thumbnail', 
            'thumbnail_small_url', 'video', 'created_at', 
            'updated_at', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']

    def get_thumbnail_small_url(self, obj):
        if obj.thumbnail:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'thumbnail', 'thumbnail_small')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.thumbnail_small.url
            except:
                return None
        return None

    def _lang(self):
        return self.context.get('lang') or get_language() or 'en'

    def get_title(self, obj):
        return obj.safe_translation_getter('title', language_code=self._lang(), any_language=True)

    def get_description(self, obj):
        return obj.safe_translation_getter('description', language_code=self._lang(), any_language=True)

class ArticleSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_url = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'body', 'image', 
            'thumbnail_url', 'created_at', 'updated_at', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']

    def get_thumbnail_url(self, obj):
        if obj.image:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'image', 'thumbnail')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.thumbnail.url
            except:
                return None
        return None 
    
    def _lang(self):
        return self.context.get('lang') or get_language() or 'en'

    def get_title(self, obj):
        return obj.safe_translation_getter('title', language_code=self._lang(), any_language=True)

    def get_body(self, obj):
        return obj.safe_translation_getter('body', language_code=self._lang(), any_language=True)
    
class RecipeSerializer(ArticleSerializer):
    title = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    time_required = serializers.SerializerMethodField()
    serves = serializers.SerializerMethodField()
    ingredients = serializers.SerializerMethodField()
    directions = serializers.SerializerMethodField()
    class Meta:
        model = Recipe
        fields = [
            'id', 'title', 'description', 'image', 'thumbnail_url', 'created_at', 'updated_at',
            'time_required', 'serves', 'ingredients', 'directions', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']

    def _lang(self):
        return self.context.get('lang') or get_language() or 'en'

    def get_title(self, obj):
        return obj.safe_translation_getter('title', language_code=self._lang(), any_language=True)

    def get_description(self, obj):
        return obj.safe_translation_getter('description', language_code=self._lang(), any_language=True)

    def get_time_required(self, obj):
        return obj.safe_translation_getter('time_required', language_code=self._lang(), any_language=True)

    def get_serves(self, obj):
        return obj.safe_translation_getter('serves', language_code=self._lang(), any_language=True)

    def get_ingredients(self, obj):
        return obj.safe_translation_getter('ingredients', language_code=self._lang(), any_language=True)

    def get_directions(self, obj):
        return obj.safe_translation_getter('directions', language_code=self._lang(), any_language=True)


class DevotionalSetSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    fast_name = serializers.CharField(source='fast.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    number_of_days = serializers.ReadOnlyField()
    is_bookmarked = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    
    class Meta:
        model = DevotionalSet
        fields = [
            'id', 'title', 'description', 'fast', 'fast_name',
            'image', 'thumbnail_url', 'number_of_days',
            'created_at', 'updated_at', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']

    def get_thumbnail_url(self, obj):
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

    def _lang(self):
        return self.context.get('lang') or get_language() or 'en'

    def get_title(self, obj):
        return obj.safe_translation_getter('title', language_code=self._lang(), any_language=True)

    def get_description(self, obj):
        return obj.safe_translation_getter('description', language_code=self._lang(), any_language=True)


class BookmarkSerializer(serializers.ModelSerializer):
    """Serializer for listing user bookmarks."""
    
    content = serializers.SerializerMethodField()
    content_type_name = serializers.ReadOnlyField()
    
    class Meta:
        model = Bookmark
        fields = [
            'id', 'content_type_name', 'object_id', 'content', 
            'note', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_content(self, obj):
        """Get the representation of the bookmarked content."""
        return obj.get_content_representation()


class BookmarkCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating bookmarks with generic foreign key support."""
    
    content_type = serializers.CharField(write_only=True)
    
    class Meta:
        model = Bookmark
        fields = ['content_type', 'object_id', 'note']
    
    def validate_content_type(self, value):
        """Validate that the content type is allowed for bookmarking."""
        allowed_models = [
            'video', 'article', 'recipe', 'devotionalset', 
            'devotional', 'fast', 'reading'
        ]
        
        if value.lower() not in allowed_models:
            raise serializers.ValidationError(
                f"Content type '{value}' is not allowed for bookmarking. "
                f"Allowed types: {', '.join(allowed_models)}"
            )
        
        try:
            # Try to get the ContentType for the model
            if value.lower() == 'devotionalset':
                content_type = ContentType.objects.get(
                    app_label='hub', model='devotionalset'
                )
            elif value.lower() in ['devotional', 'fast', 'reading']:
                content_type = ContentType.objects.get(
                    app_label='hub', model=value.lower()
                )
            else:
                content_type = ContentType.objects.get(
                    app_label='learning_resources', model=value.lower()
                )
            return content_type
        except ContentType.DoesNotExist:
            raise serializers.ValidationError(f"Invalid content type: {value}")
    
    def validate(self, attrs):
        """Validate that the object exists and the user hasn't already bookmarked it."""
        content_type = attrs['content_type']
        object_id = attrs['object_id']
        
        # Check if the object exists
        model_class = content_type.model_class()
        try:
            model_class.objects.get(pk=object_id)
        except model_class.DoesNotExist:
            raise serializers.ValidationError(
                f"Object with id {object_id} does not exist for {content_type.model}"
            )
        
        # Check if user has already bookmarked this item
        user = self.context['request'].user
        if Bookmark.objects.filter(
            user=user, 
            content_type=content_type, 
            object_id=object_id
        ).exists():
            raise serializers.ValidationError(
                "You have already bookmarked this item."
            )
        
        return attrs
    
    def create(self, validated_data):
        """Create a new bookmark for the authenticated user."""
        user = self.context['request'].user
        return Bookmark.objects.create(user=user, **validated_data)
