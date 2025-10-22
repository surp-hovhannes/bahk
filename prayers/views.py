"""Views for prayers app."""
from django.db.models import Q
from django.utils.translation import activate, get_language_from_request
from rest_framework import generics
from rest_framework.permissions import AllowAny

from learning_resources.cache import BookmarkCacheManager
from prayers.models import Prayer, PrayerSet
from prayers.serializers import (
    PrayerSerializer,
    PrayerSetSerializer,
    PrayerSetListSerializer
)


class PrayerListView(generics.ListAPIView):
    """
    API endpoint that allows prayers to be viewed.

    Permissions:
        - GET: Any user can view prayers
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter prayers by matching text in title or text content.
                       Case-insensitive partial matches are supported.
        - church (int): Optional. Filter prayers by church ID.
        - category (str): Optional. Filter prayers by category (morning, evening, meal, etc.).
        - tags (str): Optional. Filter prayers by tag name(s). Comma-separated for multiple tags.
        - fast (int): Optional. Filter prayers by fast ID.

    Returns:
        A JSON response with paginated prayer results.

    Example Requests:
        GET /api/prayers/
        GET /api/prayers/?search=lord
        GET /api/prayers/?church=1
        GET /api/prayers/?category=morning
        GET /api/prayers/?tags=daily,thanksgiving
        GET /api/prayers/?fast=1
    """
    serializer_class = PrayerSerializer
    permission_classes = [AllowAny]
    
    def get_serializer_context(self):
        """Add bookmark cache data to context for performance optimization."""
        context = super().get_serializer_context()
        context['use_bookmark_cache'] = True
        return context
    
    def get_queryset(self):
        """Get filtered and ordered queryset of prayers."""
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = Prayer.objects.select_related('church', 'fast').prefetch_related('tags')
        
        # Apply search filter if provided
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(text__icontains=search)
            )
        
        # Filter by church
        church_id = self.request.query_params.get('church', None)
        if church_id:
            try:
                queryset = queryset.filter(church_id=int(church_id))
            except ValueError:
                return Prayer.objects.none()
        
        # Filter by category
        category = self.request.query_params.get('category', None)
        if category:
            queryset = queryset.filter(category=category)
        
        # Filter by tags (supports multiple tags separated by comma)
        tags = self.request.query_params.get('tags', None)
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        # Filter by fast
        fast_id = self.request.query_params.get('fast', None)
        if fast_id:
            try:
                queryset = queryset.filter(fast_id=int(fast_id))
            except ValueError:
                return Prayer.objects.none()
        
        return queryset.distinct().order_by('-created_at')


class PrayerDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single prayer to be viewed.

    Permissions:
        - GET: Any user can view prayer details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the prayer details including translations.

    Example Requests:
        GET /api/prayers/1/
        GET /api/prayers/1/?lang=hy
    """
    serializer_class = PrayerSerializer
    permission_classes = [AllowAny]
    queryset = Prayer.objects.select_related('church', 'fast').prefetch_related('tags')


class PrayerSetListView(generics.ListAPIView):
    """
    API endpoint that allows prayer sets to be viewed.

    Permissions:
        - GET: Any user can view prayer sets
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter prayer sets by matching text in title or description.
                       Case-insensitive partial matches are supported.
        - church (int): Optional. Filter prayer sets by church ID.
        - category (str): Optional. Filter prayer sets by category (morning, evening, general).

    Returns:
        A JSON response with paginated prayer set results (without full prayer details).

    Example Requests:
        GET /api/prayer-sets/
        GET /api/prayer-sets/?search=morning
        GET /api/prayer-sets/?church=1
        GET /api/prayer-sets/?category=morning
    """
    serializer_class = PrayerSetListSerializer
    permission_classes = [AllowAny]
    
    def get_serializer_context(self):
        """Add bookmark cache data to context for performance optimization."""
        context = super().get_serializer_context()
        context['use_bookmark_cache'] = True
        return context
    
    def get_queryset(self):
        """Get filtered and ordered queryset of prayer sets."""
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = PrayerSet.objects.select_related('church')
        
        # Apply search filter if provided
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search)
            )
        
        # Filter by church
        church_id = self.request.query_params.get('church', None)
        if church_id:
            try:
                queryset = queryset.filter(church_id=int(church_id))
            except ValueError:
                return PrayerSet.objects.none()
        
        # Filter by category
        category = self.request.query_params.get('category', None)
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset.order_by('-created_at')


class PrayerSetDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single prayer set to be viewed with all prayers.

    Permissions:
        - GET: Any user can view prayer set details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the prayer set details including all ordered prayers.

    Example Requests:
        GET /api/prayer-sets/1/
        GET /api/prayer-sets/1/?lang=hy
    """
    serializer_class = PrayerSetSerializer
    permission_classes = [AllowAny]
    
    def get_serializer_context(self):
        """Add bookmark cache data to context for performance optimization."""
        context = super().get_serializer_context()
        context['use_bookmark_cache'] = True
        return context
    
    def get_queryset(self):
        """Optimize queryset with prefetch for prayers."""
        return PrayerSet.objects.select_related('church').prefetch_related(
            'memberships__prayer__church',
            'memberships__prayer__fast',
            'memberships__prayer__tags'
        )

