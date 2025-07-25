from django.shortcuts import render
from rest_framework import generics
from rest_framework.permissions import AllowAny
from django.db.models import Q
from .models import Video, Article, Recipe
from .serializers import VideoSerializer, ArticleSerializer, RecipeSerializer, DevotionalSetSerializer
from hub.models import DevotionalSet


class VideoListView(generics.ListAPIView):
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
                    "updated_at": "2024-03-14T12:00:00Z"
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
        return queryset.order_by('-created_at')

class ArticleListView(generics.ListAPIView):
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
        queryset = Article.objects.all()
        search = self.request.query_params.get('search', None)
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(body__icontains=search)
            )
        return queryset.order_by('-created_at')


class RecipeListView(generics.ListAPIView):
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
        queryset = Recipe.objects.all()
        search = self.request.query_params.get('search', None)
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(body__icontains=search) |
                Q(ingredients__icontains=search)
            )
        return queryset.order_by('-created_at')


class DevotionalSetListView(generics.ListAPIView):
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
