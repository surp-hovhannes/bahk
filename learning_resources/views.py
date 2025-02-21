from django.shortcuts import render
from rest_framework import generics
from rest_framework.permissions import AllowAny
from django.db.models import Q
from .models import Video, Article
from .serializers import VideoSerializer, ArticleSerializer


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
        queryset = Video.objects.all()
        search = self.request.query_params.get('search', None)
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
