from django.shortcuts import render
from rest_framework import generics
from rest_framework.permissions import AllowAny
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from .models import Video, Article
from .serializers import VideoSerializer, ArticleSerializer

@method_decorator(cache_page(60 * 15), name='list')  # Cache for 15 minutes
class VideoListView(generics.ListAPIView):
    """
    API endpoint that allows videos to be viewed.

    Permissions:
        - GET: Any user can view videos
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter videos by matching text in title or description.
                       Case-insensitive partial matches are supported.

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
    """
    serializer_class = VideoSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        queryset = Video.objects.only(
            'id', 'title', 'thumbnail', 'video', 
            'created_at', 'updated_at'
        )  # Only fetch needed fields
        search = self.request.query_params.get('search', None)
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search)
            ).only(
                'id', 'title', 'description', 'thumbnail', 
                'video', 'created_at', 'updated_at'
            )  # Include description when searching
        return queryset.order_by('-created_at')[:10]

@method_decorator(cache_page(60 * 15), name='list')  # Cache for 15 minutes
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
        queryset = Article.objects.only(
            'id', 'title', 'image', 
            'created_at', 'updated_at'
        )  # Only fetch needed fields
        search = self.request.query_params.get('search', None)
        if search is not None:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(body__icontains=search)
            ).only(
                'id', 'title', 'body', 'image', 
                'created_at', 'updated_at'
            )  # Include body when searching
        return queryset.order_by('-created_at')[:10]
