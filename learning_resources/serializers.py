from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType
from hub.mixins import ThumbnailCacheMixin
from .models import Article, Recipe, Video, Bookmark
from django.utils.translation import activate
from django.conf import settings
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
            except Exception:
                return None
        return None

    def to_representation(self, instance):
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        data['description'] = getattr(instance, 'description_i18n', instance.description)
        return data


class DevotionalVideoWriteSerializer(serializers.ModelSerializer):
    """Strict staff-only input contract; storage names are capability-derived."""

    upload_token = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    clear_thumbnail = serializers.BooleanField(write_only=True, required=False, default=False)
    language_code = serializers.ChoiceField(
        choices=tuple(dict.fromkeys([
            *getattr(settings, "MODELTRANS_AVAILABLE_LANGUAGES", []),
            *(code for code, _name in getattr(settings, "LANGUAGES", [])),
        ]))
    )

    class Meta:
        model = Video
        fields = [
            "title", "description", "language_code", "upload_token",
            "thumbnail", "clear_thumbnail",
        ]
        extra_kwargs = {
            "title": {"allow_blank": False, "max_length": 200},
            "description": {"allow_blank": False},
            "thumbnail": {"required": False, "allow_null": False},
        }

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: "Unknown field." for name in sorted(unknown)})
        if self.partial and not self.initial_data:
            raise serializers.ValidationError("PATCH body must contain at least one field.")
        if not self.partial and "upload_token" not in attrs:
            raise serializers.ValidationError({"upload_token": "This field is required."})
        if not self.partial and "clear_thumbnail" in self.initial_data:
            raise serializers.ValidationError({"clear_thumbnail": "This field is only allowed on PATCH."})
        if attrs.get("clear_thumbnail") and "thumbnail" in attrs:
            raise serializers.ValidationError("thumbnail and clear_thumbnail are mutually exclusive.")
        return attrs

    def _attach(self, token, save):
        from .devotional_video_uploads import UploadError, attach_upload_token
        try:
            return attach_upload_token(
                token, user_id=self.context["request"].user.pk, save=save
            )
        except UploadError as exc:
            raise serializers.ValidationError({"upload_token": str(exc)}) from exc

    def create(self, validated_data):
        token = validated_data.pop("upload_token")
        validated_data.pop("clear_thumbnail", None)
        validated_data["category"] = "devotional"
        return self._attach(
            token,
            lambda key: super(DevotionalVideoWriteSerializer, self).create(
                {**validated_data, "video": key}
            ),
        )

    def update(self, instance, validated_data):
        token = validated_data.pop("upload_token", None)
        clear_thumbnail = validated_data.pop("clear_thumbnail", False)
        if token:
            validated_data["video"] = None
        if clear_thumbnail:
            validated_data["thumbnail"] = None
        instance.category = "devotional"
        def save(key=None):
            if key is not None:
                validated_data["video"] = key
            return super(DevotionalVideoWriteSerializer, self).update(
                instance, validated_data
            )

        return self._attach(token, save) if token else save()

class ArticleSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    thumbnail_url = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()

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
            except Exception:
                return None
        return None 
    
    def to_representation(self, instance):
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        # Only Article has body; guard for Recipe which subclasses ArticleSerializer
        if hasattr(instance, 'body'):
            data['body'] = getattr(instance, 'body_i18n', instance.body)
        return data
    
class RecipeSerializer(ArticleSerializer):
    class Meta:
        model = Recipe
        fields = [
            'id', 'title', 'description', 'image', 'thumbnail_url', 'created_at', 'updated_at',
            'time_required', 'serves', 'ingredients', 'directions', 'is_bookmarked'
        ]
        read_only_fields = ['created_at', 'updated_at', 'is_bookmarked']

    def to_representation(self, instance):
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        # Override translations for recipe-specific fields
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        data['description'] = getattr(instance, 'description_i18n', instance.description)
        data['time_required'] = getattr(instance, 'time_required_i18n', instance.time_required)
        data['serves'] = getattr(instance, 'serves_i18n', instance.serves)
        data['ingredients'] = getattr(instance, 'ingredients_i18n', instance.ingredients)
        data['directions'] = getattr(instance, 'directions_i18n', instance.directions)
        return data


class DevotionalSetSerializer(BookmarkOptimizedSerializerMixin, serializers.ModelSerializer, ThumbnailCacheMixin):
    fast_name = serializers.CharField(source='fast.name', read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    number_of_days = serializers.ReadOnlyField()
    is_bookmarked = serializers.SerializerMethodField()
    
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

    def to_representation(self, instance):
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        data['title'] = getattr(instance, 'title_i18n', instance.title)
        data['description'] = getattr(instance, 'description_i18n', instance.description)
        return data


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
            'devotional', 'fast', 'reading', 'prayer', 'prayerset'
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
            elif value.lower() in ['prayer', 'prayerset']:
                content_type = ContentType.objects.get(
                    app_label='prayers', model=value.lower()
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
