from django.shortcuts import render
from rest_framework import generics
from rest_framework.permissions import AllowAny
from django.db.models import Q
from .models import Video, Article, Recipe
from .serializers import VideoSerializer, ArticleSerializer, RecipeSerializer


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
        - language (str): Optional. Language code for translations (e.g., 'en', 'am').
                         Defaults to 'en' if not specified. Only returns videos that have 
                         translations in the specified language.

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
        GET /api/learning-resources/videos/?language=am
        GET /api/learning-resources/videos/?category=devotional&language=am
    """
    serializer_class = VideoSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        queryset = Video.objects.all()
        search = self.request.query_params.get('search', None)
        category = self.request.query_params.get('category', 'general')
        language = self.request.query_params.get('language', 'en')
        
        # Filter by category
        queryset = queryset.filter(category=category)
        
        # Filter by language availability
        if language != 'en':
            # For non-English languages, only return items that have translations
            queryset = queryset.filter(translations__language_code=language).distinct()
        
        # Apply search filter if provided
        if search is not None:
            if language == 'en':
                # Search in default fields for English
                queryset = queryset.filter(
                    Q(title__icontains=search) |
                    Q(description__icontains=search)
                )
            else:
                # Search in translated fields for other languages
                queryset = queryset.filter(
                    Q(translations__title__icontains=search) |
                    Q(translations__description__icontains=search)
                ).filter(translations__language_code=language).distinct()
        
        return queryset.order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        language = self.request.query_params.get('language', 'en')
        context['language'] = language
        return context


class ArticleListView(generics.ListAPIView):
    """
    API endpoint that allows articles to be viewed.

    Permissions:
        - GET: Any user can view articles
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter articles by matching text in title or body.
                       Case-insensitive partial matches are supported.
        - language (str): Optional. Language code for translations (e.g., 'en', 'am').
                         Defaults to 'en' if not specified. Only returns articles that have 
                         translations in the specified language.

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
        GET /api/learning-resources/articles/?language=am
    """
    serializer_class = ArticleSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        queryset = Article.objects.all()
        search = self.request.query_params.get('search', None)
        language = self.request.query_params.get('language', 'en')
        
        # Filter by language availability
        if language != 'en':
            # For non-English languages, only return items that have translations
            queryset = queryset.filter(translations__language_code=language).distinct()
        
        # Apply search filter if provided
        if search is not None:
            if language == 'en':
                # Search in default fields for English
                queryset = queryset.filter(
                    Q(title__icontains=search) |
                    Q(body__icontains=search)
                )
            else:
                # Search in translated fields for other languages
                queryset = queryset.filter(
                    Q(translations__title__icontains=search) |
                    Q(translations__body__icontains=search)
                ).filter(translations__language_code=language).distinct()
        
        return queryset.order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        language = self.request.query_params.get('language', 'en')
        context['language'] = language
        return context


class RecipeListView(generics.ListAPIView):
    """
    API endpoint that allows recipes to be viewed.

    Permissions:
        - GET: Any user can view recipes
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter recipes by matching text in title, description, or ingredients.
                       Case-insensitive partial matches are supported.
        - language (str): Optional. Language code for translations (e.g., 'en', 'am').
                         Defaults to 'en' if not specified. Only returns recipes that have 
                         translations in the specified language.

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
                    "description": "Recipe Description Text",
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
        GET /api/learning-resources/recipes/?language=am
    """
    serializer_class = RecipeSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        queryset = Recipe.objects.all()
        search = self.request.query_params.get('search', None)
        language = self.request.query_params.get('language', 'en')
        
        # Filter by language availability
        if language != 'en':
            # For non-English languages, only return items that have translations
            queryset = queryset.filter(translations__language_code=language).distinct()
        
        # Apply search filter if provided
        if search is not None:
            if language == 'en':
                # Search in default fields for English
                queryset = queryset.filter(
                    Q(title__icontains=search) |
                    Q(description__icontains=search) |
                    Q(ingredients__icontains=search)
                )
            else:
                # Search in translated fields for other languages
                queryset = queryset.filter(
                    Q(translations__title__icontains=search) |
                    Q(translations__description__icontains=search) |
                    Q(translations__ingredients__icontains=search)
                ).filter(translations__language_code=language).distinct()
        
        return queryset.order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        language = self.request.query_params.get('language', 'en')
        context['language'] = language
        return context
