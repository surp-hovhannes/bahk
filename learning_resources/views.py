from django.shortcuts import render, get_object_or_404
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, Prefetch
import logging
from .models import Video, Article, Recipe, Bookmark
from .serializers import (
    VideoSerializer, ArticleSerializer, RecipeSerializer, 
    DevotionalSetSerializer, BookmarkSerializer, BookmarkCreateSerializer
)
from .cache import BookmarkCacheManager
from hub.models import DevotionalSet
from django.utils.translation import activate, get_language_from_request


class BookmarkOptimizedMixin:
    """
    Mixin to optimize bookmark queries using Redis caching.
    
    This approach uses Redis cache to provide sub-millisecond bookmark lookups,
    with automatic fallback to database queries when needed.
    
    Uses explicit context passing instead of storing data in the request object
    to avoid memory issues and improve testability.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cache data for the current paginated page to avoid memory issues
        # This is cleared after each pagination to prevent accumulation
        self._bookmark_cache_data = None
    
    def get_serializer_context(self):
        """Add Redis bookmark cache context with explicit cache data."""
        context = super().get_serializer_context()
        
        # Enable Redis caching for authenticated users
        if hasattr(self.request, 'user') and self.request.user.is_authenticated:
            context['use_bookmark_cache'] = True
            # Pass cached bookmark data explicitly through context
            if self._bookmark_cache_data is not None:
                context['bookmark_cache_data'] = self._bookmark_cache_data
            
        return context
    
    def paginate_queryset(self, queryset):
        """Override to preload bookmarks using Redis cache."""
        page = super().paginate_queryset(queryset)
        
        if page is not None and hasattr(self.request, 'user') and self.request.user.is_authenticated:
            # Use Redis cache for ultra-fast bookmark lookups
            self._bookmark_cache_data = BookmarkCacheManager.get_bookmarks_for_objects(
                self.request.user, 
                page
            )
            # Note: The cache data will be passed to serializers via get_serializer_context
        
        return page
    
    def get_paginated_response(self, data):
        """Override to clear cache data after response generation."""
        response = super().get_paginated_response(data)
        
        # Clear cache data to prevent memory accumulation
        # This is especially important for large datasets
        self._bookmark_cache_data = None
        
        return response
    
    def get_object(self):
        """Override to preload bookmarks for single object retrieval."""
        obj = super().get_object()
        
        # For single object retrieval, load bookmark data directly
        if hasattr(self.request, 'user') and self.request.user.is_authenticated:
            self._bookmark_cache_data = BookmarkCacheManager.get_bookmarks_for_objects(
                self.request.user, 
                [obj]
            )
        
        return obj


class VideoListView(BookmarkOptimizedMixin, generics.ListAPIView):
    """
    API endpoint that allows videos to be viewed.

    Permissions:
        - GET: Any user can view videos
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter videos by matching text in title or description.
                       Case-insensitive partial matches are supported.
        - category (str): Optional. Filter videos by category ('general', 'devotional', 'tutorial').
                         Defaults to 'general' if not specified.

    Returns:
        A JSON response with the following structure:
        {
            "count": 123,
            "next": "http://api.example.org/videos/?page=4",
            "previous": "http://api.example.org/videos/?page=2",
            "results": [
                {
                    "id": 1,
                    "title": "Video Title",
                    "description": "Video description text",
                    "category": "general",
                    "thumbnail": "/media/videos/thumbnails/thumb.jpg",
                    "thumbnail_small_url": "/media/CACHE/images/videos/thumbnails/thumb/123.jpg",
                    "video": "/media/videos/video.mp4",
                    "created_at": "2024-03-14T12:00:00Z",
                    "updated_at": "2024-03-14T12:00:00Z",
                    "is_bookmarked": true
                },
                ...
            ]
        }

    Example Requests:
        GET /api/learning-resources/videos/
        GET /api/learning-resources/videos/?search=prayer
        GET /api/learning-resources/videos/?category=tutorial
    """
    serializer_class = VideoSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        queryset = Video.objects.all()
        search = self.request.query_params.get('search', None)
        category = self.request.query_params.get('category', 'general')
        
        # Filter by category
        queryset = queryset.filter(category=category)
        
        # Apply search filter if provided
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search)
            )
        queryset = queryset.order_by('-created_at')
        qs_lang = queryset.filter(language_code=lang)
        return qs_lang if qs_lang.exists() else queryset.filter(language_code='en')

class ArticleListView(BookmarkOptimizedMixin, generics.ListAPIView):
    """
    API endpoint that allows articles to be viewed.

    Permissions:
        - GET: Any user can view articles
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter articles by matching text in title or body.
                       Case-insensitive partial matches are supported.

    Returns:
        A JSON response with the following structure:
        {
            "count": 123,
            "next": "http://api.example.org/articles/?page=4",
            "previous": "http://api.example.org/articles/?page=2",
            "results": [
                {
                    "id": 1,
                    "title": "Article Title",
                    "body": "Article body in markdown format",
                    "image": "/media/articles/images/main.jpg",
                    "thumbnail_url": "/media/CACHE/images/articles/images/main/123.jpg",
                    "created_at": "2024-03-14T12:00:00Z",
                    "updated_at": "2024-03-14T12:00:00Z"
                },
                ...
            ]
        }

    Example Requests:
        GET /api/learning-resources/articles/
        GET /api/learning-resources/articles/?search=fasting
    """
    serializer_class = ArticleSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        queryset = Article.objects.all()
        search = self.request.query_params.get('search', None)
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(body__icontains=search)
            )
        return queryset.order_by('-created_at')


class RecipeListView(BookmarkOptimizedMixin, generics.ListAPIView):
    """
    API endpoint that allows recipes to be viewed.

    Permissions:
        - GET: Any user can view recipes
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter recipes by matching text in title, body, or ingredients.
                       Case-insensitive partial matches are supported.

    Returns:
        A JSON response with the following structure:
        {
            "count": 123,
            "next": "http://api.example.org/recipes/?page=4",
            "previous": "http://api.example.org/recipes/?page=2",
            "results": [
                {
                    "id": 1,
                    "title": "Recipe Title",
                    "description": "Recipe Descriptioni Text",
                    "image": "/media/articles/images/main.jpg",
                    "thumbnail_url": "/media/CACHE/images/articles/images/main/123.jpg",
                    "created_at": "2024-03-14T12:00:00Z",
                    "updated_at": "2024-03-14T12:00:00Z"
                    "time_required": "30 minutes",
                    "serves": "4-6 people",
                    "ingredients": "List of ingredients",
                    "directions": "Directions for preparing the recipe"
                },
                ...
            ]
        }

    Example Requests:
        GET /api/learning-resources/recipes/
        GET /api/learning-resources/recipes/?search=garbanzo
    """
    serializer_class = RecipeSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        queryset = Recipe.objects.all()
        search = self.request.query_params.get('search', None)
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(body__icontains=search) |
                Q(ingredients__icontains=search)
            )
        return queryset.order_by('-created_at')


class VideoDetailView(BookmarkOptimizedMixin, generics.RetrieveAPIView):
    """
    API endpoint that allows a single video to be viewed.

    Permissions:
        - GET: Any user can view video details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the video details:
        {
            "id": 1,
            "title": "Video Title",
            "description": "Video description text",
            "category": "general",
            "thumbnail": "/media/videos/thumbnails/thumb.jpg",
            "thumbnail_small_url": "/media/CACHE/images/videos/thumbnails/thumb/123.jpg",
            "video": "/media/videos/video.mp4",
            "created_at": "2024-03-14T12:00:00Z",
            "updated_at": "2024-03-14T12:00:00Z",
            "is_bookmarked": true
        }

    Example Requests:
        GET /api/learning-resources/videos/1/
    """
    serializer_class = VideoSerializer
    permission_classes = [AllowAny]
    queryset = Video.objects.all()

    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


class ArticleDetailView(BookmarkOptimizedMixin, generics.RetrieveAPIView):
    """
    API endpoint that allows a single article to be viewed.

    Permissions:
        - GET: Any user can view article details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the article details:
        {
            "id": 1,
            "title": "Article Title",
            "body": "Article body in markdown format",
            "image": "/media/articles/images/main.jpg",
            "thumbnail_url": "/media/CACHE/images/articles/images/main/123.jpg",
            "created_at": "2024-03-14T12:00:00Z",
            "updated_at": "2024-03-14T12:00:00Z",
            "is_bookmarked": true
        }

    Example Requests:
        GET /api/learning-resources/articles/1/
    """
    serializer_class = ArticleSerializer
    permission_classes = [AllowAny]
    queryset = Article.objects.all()

    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


class RecipeDetailView(BookmarkOptimizedMixin, generics.RetrieveAPIView):
    """
    API endpoint that allows a single recipe to be viewed.

    Permissions:
        - GET: Any user can view recipe details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the recipe details:
        {
            "id": 1,
            "title": "Recipe Title",
            "description": "Recipe Description Text",
            "image": "/media/recipes/images/main.jpg",
            "thumbnail_url": "/media/CACHE/images/recipes/images/main/123.jpg",
            "time_required": "30 minutes",
            "serves": "4-6 people",
            "ingredients": "List of ingredients",
            "directions": "Directions for preparing the recipe",
            "created_at": "2024-03-14T12:00:00Z",
            "updated_at": "2024-03-14T12:00:00Z",
            "is_bookmarked": true
        }

    Example Requests:
        GET /api/learning-resources/recipes/1/
    """
    serializer_class = RecipeSerializer
    permission_classes = [AllowAny]
    queryset = Recipe.objects.all()

    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


class DevotionalSetListView(BookmarkOptimizedMixin, generics.ListAPIView):
    """
    API endpoint that allows devotional sets to be viewed.

    Permissions:
        - GET: Any user can view devotional sets
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter devotional sets by matching text in title or description.
                       Case-insensitive partial matches are supported.
        - fast (int): Optional. Filter devotional sets by fast ID.

    Returns:
        A JSON response with the following structure:
        {
            "count": 123,
            "next": "http://api.example.org/devotional-sets/?page=4",
            "previous": "http://api.example.org/devotional-sets/?page=2",
            "results": [
                {
                    "id": 1,
                    "title": "Devotional Set Title",
                    "description": "Description of devotional set",
                    "fast": 1,
                    "fast_name": "Lenten Fast",
                    "image": "/media/devotional_sets/image.jpg",
                    "thumbnail_url": "/media/CACHE/images/devotional_sets/image/123.jpg",
                    "number_of_days": 40,
                    "created_at": "2024-03-14T12:00:00Z",
                    "updated_at": "2024-03-14T12:00:00Z"
                },
                ...
            ]
        }

    Example Requests:
        GET /api/learning-resources/devotional-sets/
        GET /api/learning-resources/devotional-sets/?search=lent
        GET /api/learning-resources/devotional-sets/?fast=1
    """
    serializer_class = DevotionalSetSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        queryset = DevotionalSet.objects.select_related('fast').all()
        search = self.request.query_params.get('search', None)
        fast_id = self.request.query_params.get('fast', None)
        
        # Filter by fast if provided
        if fast_id is not None:
            try:
                queryset = queryset.filter(fast_id=int(fast_id))
            except ValueError:
                # Invalid fast_id, return empty queryset
                return DevotionalSet.objects.none()
        
        # Apply search filter if provided
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(fast__name__icontains=search)
            )
        return queryset.order_by('-created_at')


class DevotionalSetDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single devotional set to be viewed.

    Permissions:
        - GET: Any user can view devotional set details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the devotional set details:
        {
            "id": 1,
            "title": "Devotional Set Title",
            "description": "Description of devotional set",
            "fast": 1,
            "fast_name": "Lenten Fast",
            "image": "/media/devotional_sets/image.jpg",
            "thumbnail_url": "/media/CACHE/images/devotional_sets/image/123.jpg",
            "number_of_days": 40,
            "created_at": "2024-03-14T12:00:00Z",
            "updated_at": "2024-03-14T12:00:00Z"
        }

    Example Requests:
        GET /api/learning-resources/devotional-sets/1/
    """
    serializer_class = DevotionalSetSerializer
    permission_classes = [AllowAny]
    queryset = DevotionalSet.objects.select_related('fast').all()

    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


class BookmarkListView(generics.ListAPIView):
    """
    API endpoint to list user's bookmarks.
    
    Permissions:
        - GET: Authenticated users only
    
    Query Parameters:
        - content_type (str): Optional. Filter bookmarks by content type 
                             (video, article, recipe, devotionalset, etc.)
    
    Returns:
        A JSON response with the following structure:
        {
            "count": 5,
            "next": null,
            "previous": null,
            "results": [
                {
                    "id": 1,
                    "content_type_name": "video",
                    "object_id": 123,
                    "content": {
                        "id": 123,
                        "type": "video",
                        "title": "Morning Prayer",
                        "description": "Daily morning prayer...",
                        "thumbnail_url": "/media/...",
                        "created_at": "2024-03-14T12:00:00Z"
                    },
                    "note": "Great prayer for daily routine",
                    "created_at": "2024-03-15T10:30:00Z"
                },
                ...
            ]
        }
    
    Example Requests:
        GET /api/learning-resources/bookmarks/
        GET /api/learning-resources/bookmarks/?content_type=video
    """
    serializer_class = BookmarkSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = Bookmark.objects.filter(user=self.request.user).select_related(
            'content_type'
        )
        
        # Filter by content type if specified
        content_type = self.request.query_params.get('content_type')
        if content_type:
            try:
                if content_type.lower() == 'devotionalset':
                    ct = ContentType.objects.get(app_label='hub', model='devotionalset')
                elif content_type.lower() in ['devotional', 'fast', 'reading']:
                    ct = ContentType.objects.get(app_label='hub', model=content_type.lower())
                else:
                    ct = ContentType.objects.get(
                        app_label='learning_resources', model=content_type.lower()
                    )
                queryset = queryset.filter(content_type=ct)
            except ContentType.DoesNotExist:
                # Return empty queryset for invalid content types
                queryset = queryset.none()
        
        return queryset


class BookmarkCreateView(generics.CreateAPIView):
    """
    API endpoint to create a new bookmark.
    
    Permissions:
        - POST: Authenticated users only
    
    Request Body:
        {
            "content_type": "video",  // Required: type of content to bookmark
            "object_id": 123,         // Required: ID of the content
            "note": "Optional note"   // Optional: user's note about the bookmark
        }
    
    Returns:
        201 Created:
        {
            "id": 1,
            "content_type_name": "video",
            "object_id": 123,
            "content": {
                "id": 123,
                "type": "video",
                "title": "Morning Prayer",
                "description": "Daily morning prayer...",
                "thumbnail_url": "/media/...",
                "created_at": "2024-03-14T12:00:00Z"
            },
            "note": "Optional note",
            "created_at": "2024-03-15T10:30:00Z"
        }
        
        400 Bad Request: If validation fails
        409 Conflict: If user has already bookmarked this item
    
    Example Requests:
        POST /api/learning-resources/bookmarks/
        {
            "content_type": "video",
            "object_id": 123,
            "note": "Great prayer for daily routine"
        }
    """
    serializer_class = BookmarkCreateSerializer
    permission_classes = [IsAuthenticated]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bookmark = serializer.save()
        
        # Update Redis cache - add bookmark
        BookmarkCacheManager.bookmark_created(
            user=request.user,
            content_type=bookmark.content_type,
            object_id=bookmark.object_id
        )
        
        # Return the full bookmark representation
        response_serializer = BookmarkSerializer(bookmark)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def bookmark_delete_view(request, content_type, object_id):
    """
    API endpoint to delete a bookmark.
    
    Permissions:
        - DELETE: Authenticated users only
    
    URL Parameters:
        - content_type (str): Type of content (video, article, recipe, etc.)
        - object_id (int): ID of the content
    
    Returns:
        204 No Content: If bookmark was successfully deleted
        404 Not Found: If bookmark doesn't exist
        400 Bad Request: If parameters are invalid
    
    Example Requests:
        DELETE /api/learning-resources/bookmarks/video/123/
    """
    # Validate object_id is a valid integer
    try:
        object_id = int(object_id)
        if object_id <= 0:
            raise ValueError("Object ID must be positive")
    except (ValueError, TypeError) as e:
        return Response(
            {'error': f'Invalid object_id: must be a positive integer'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get the content type with specific error handling
    try:
        if content_type.lower() == 'devotionalset':
            ct = ContentType.objects.get(app_label='hub', model='devotionalset')
        elif content_type.lower() in ['devotional', 'fast', 'reading']:
            ct = ContentType.objects.get(app_label='hub', model=content_type.lower())
        else:
            ct = ContentType.objects.get(
                app_label='learning_resources', model=content_type.lower()
            )
    except ContentType.DoesNotExist:
        return Response(
            {'error': f'Invalid content type: {content_type}'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Find and delete the bookmark
    bookmark = get_object_or_404(
        Bookmark,
        user=request.user,
        content_type=ct,
        object_id=object_id
    )
    
    # Update Redis cache - handle cache failures gracefully
    try:
        BookmarkCacheManager.bookmark_deleted(
            user=request.user,
            content_type=ct,
            object_id=object_id
        )
    except Exception as cache_error:
        # Log cache failure but don't fail the request
        # Cache inconsistency is acceptable for this operation
        logger = logging.getLogger(__name__)
        logger.warning(
            f"Cache update failed during bookmark deletion for user {request.user.id}, "
            f"content_type {ct.id}, object_id {object_id}: {cache_error}"
        )
    
    # Delete the bookmark from database
    try:
        bookmark.delete()
    except Exception as db_error:
        # Handle unexpected database errors
        logger = logging.getLogger(__name__)
        logger.error(
            f"Database error during bookmark deletion for user {request.user.id}, "
            f"bookmark {bookmark.id}: {db_error}"
        )
        return Response(
            {'error': 'Unable to delete bookmark due to server error'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bookmark_check_view(request, content_type, object_id):
    """
    API endpoint to check if an item is bookmarked by the user.
    
    Permissions:
        - GET: Authenticated users only
    
    URL Parameters:
        - content_type (str): Type of content (video, article, recipe, etc.)
        - object_id (int): ID of the content
    
    Returns:
        200 OK:
        {
            "is_bookmarked": true,
            "bookmark_id": 123  // Only included if bookmarked
        }
        400 Bad Request: If parameters are invalid
    
    Example Requests:
        GET /api/learning-resources/bookmarks/check/video/123/
    """
    # Validate object_id is a valid integer
    try:
        object_id = int(object_id)
        if object_id <= 0:
            raise ValueError("Object ID must be positive")
    except (ValueError, TypeError):
        return Response(
            {'error': 'Invalid object_id: must be a positive integer'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get the content type with specific error handling
    try:
        if content_type.lower() == 'devotionalset':
            ct = ContentType.objects.get(app_label='hub', model='devotionalset')
        elif content_type.lower() in ['devotional', 'fast', 'reading']:
            ct = ContentType.objects.get(app_label='hub', model=content_type.lower())
        else:
            ct = ContentType.objects.get(
                app_label='learning_resources', model=content_type.lower()
            )
    except ContentType.DoesNotExist:
        return Response(
            {'error': f'Invalid content type: {content_type}'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if bookmark exists with specific error handling
    try:
        bookmark = Bookmark.objects.get(
            user=request.user,
            content_type=ct,
            object_id=object_id
        )
        return Response({
            'is_bookmarked': True,
            'bookmark_id': bookmark.id
        })
    except Bookmark.DoesNotExist:
        return Response({'is_bookmarked': False})
    except Exception as db_error:
        # Handle unexpected database errors
        logger = logging.getLogger(__name__)
        logger.error(
            f"Database error during bookmark check for user {request.user.id}, "
            f"content_type {ct.id}, object_id {object_id}: {db_error}"
        )
        return Response(
            {'error': 'Unable to check bookmark due to server error'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
