"""Views for patristic quotes."""
import hashlib
from datetime import datetime

from django.core.cache import cache
from django.db.models import Q
from django.utils.translation import activate, get_language_from_request
from rest_framework import generics, views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from hub.models import PatristicQuote
from hub.serializers import PatristicQuoteSerializer


class PatristicQuoteListView(generics.ListAPIView):
    """
    API endpoint that allows patristic quotes to be viewed.

    Permissions:
        - GET: Any user can view quotes
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter quotes by matching text in quote text or attribution.
                       Case-insensitive partial matches are supported.
        - church (int): Optional. Filter quotes by church ID.
        - fast (int): Optional. Filter quotes by fast ID.
        - tags (str): Optional. Filter quotes by tag name(s). Comma-separated for multiple tags.

    Returns:
        A JSON response with paginated quote results.

    Example Requests:
        GET /api/patristic-quotes/
        GET /api/patristic-quotes/?search=prayer
        GET /api/patristic-quotes/?church=1
        GET /api/patristic-quotes/?fast=1
        GET /api/patristic-quotes/?tags=fasting,humility
    """
    serializer_class = PatristicQuoteSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        """Get filtered and ordered queryset of patristic quotes."""
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = PatristicQuote.objects.prefetch_related(
            'churches', 'fasts', 'tags'
        )
        
        # Apply search filter if provided
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(text__icontains=search) |
                Q(attribution__icontains=search)
            )
        
        # Filter by church
        church_id = self.request.query_params.get('church', None)
        if church_id:
            try:
                queryset = queryset.filter(churches__id=int(church_id))
            except ValueError:
                return PatristicQuote.objects.none()
        
        # Filter by fast
        fast_id = self.request.query_params.get('fast', None)
        if fast_id:
            try:
                queryset = queryset.filter(fasts__id=int(fast_id))
            except ValueError:
                return PatristicQuote.objects.none()
        
        # Filter by tags (supports multiple tags separated by comma)
        tags = self.request.query_params.get('tags', None)
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        return queryset.distinct().order_by('-created_at')


class PatristicQuoteDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single patristic quote to be viewed.

    Permissions:
        - GET: Any user can view quote details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the quote details including translations.

    Example Requests:
        GET /api/patristic-quotes/1/
        GET /api/patristic-quotes/1/?lang=hy
    """
    serializer_class = PatristicQuoteSerializer
    permission_classes = [AllowAny]
    queryset = PatristicQuote.objects.prefetch_related('churches', 'fasts', 'tags')


class PatristicQuotesByChurchView(generics.ListAPIView):
    """
    API endpoint to retrieve patristic quotes filtered by church.

    Permissions:
        - GET: Any user can view quotes
        - POST/PUT/PATCH/DELETE: Not supported

    URL Parameters:
        - church_id (int): The church ID to filter by

    Query Parameters:
        - tags (str): Optional. Filter quotes by tag name(s). Comma-separated for multiple tags.

    Returns:
        A JSON response with paginated quote results for the specified church.

    Example Requests:
        GET /api/patristic-quotes/by-church/1/
        GET /api/patristic-quotes/by-church/1/?tags=prayer
    """
    serializer_class = PatristicQuoteSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        """Get quotes filtered by church."""
        church_id = self.kwargs.get('church_id')
        
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = PatristicQuote.objects.filter(
            churches__id=church_id
        ).prefetch_related('churches', 'fasts', 'tags')
        
        # Filter by tags if provided
        tags = self.request.query_params.get('tags', None)
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        return queryset.distinct().order_by('-created_at')


class PatristicQuotesByFastView(generics.ListAPIView):
    """
    API endpoint to retrieve patristic quotes filtered by fast.

    Permissions:
        - GET: Any user can view quotes
        - POST/PUT/PATCH/DELETE: Not supported

    URL Parameters:
        - fast_id (int): The fast ID to filter by

    Query Parameters:
        - tags (str): Optional. Filter quotes by tag name(s). Comma-separated for multiple tags.

    Returns:
        A JSON response with paginated quote results for the specified fast.

    Example Requests:
        GET /api/patristic-quotes/by-fast/1/
        GET /api/patristic-quotes/by-fast/1/?tags=humility
    """
    serializer_class = PatristicQuoteSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        """Get quotes filtered by fast."""
        fast_id = self.kwargs.get('fast_id')
        
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = PatristicQuote.objects.filter(
            fasts__id=fast_id
        ).prefetch_related('churches', 'fasts', 'tags')
        
        # Filter by tags if provided
        tags = self.request.query_params.get('tags', None)
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        return queryset.distinct().order_by('-created_at')


class PatristicQuoteOfTheDayView(views.APIView):
    """
    API endpoint that returns a deterministic "quote of the day".
    
    This endpoint uses a deterministic algorithm to ensure all users
    see the same quote for a given day, fast, and tag combination.
    
    Permissions:
        - GET: Any user can view the quote of the day
        - POST/PUT/PATCH/DELETE: Not supported
    
    Query Parameters:
        - fast_id (int): Optional. Filter quotes by fast ID.
        - tags (str): Optional. Filter quotes by tag name(s). Comma-separated for multiple tags.
        - lang (str): Optional. Language code for translations (e.g., 'en', 'hy').
    
    Algorithm:
        1. Get current date in user's timezone (or UTC if not authenticated)
        2. Filter quotes by fast_id and/or tags if provided
        3. Create a deterministic seed from: date + fast_id + sorted tags
        4. Hash the seed and convert to integer
        5. Use modulo to select a specific quote from the filtered set
        6. Cache the result for 24 hours
    
    Returns:
        A JSON response with a single patristic quote.
        Returns 404 if no quotes match the criteria.
    
    Example Requests:
        GET /api/patristic-quotes/quote-of-the-day/
        GET /api/patristic-quotes/quote-of-the-day/?fast_id=1
        GET /api/patristic-quotes/quote-of-the-day/?tags=prayer,fasting
        GET /api/patristic-quotes/quote-of-the-day/?fast_id=1&tags=humility&lang=hy
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Get the quote of the day using deterministic selection."""
        # Get query parameters
        fast_id = request.query_params.get('fast_id', None)
        tags_param = request.query_params.get('tags', None)
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        
        # Activate language for translations
        activate(lang)
        
        # Get current date in user's timezone if authenticated, otherwise UTC
        if request.user.is_authenticated and hasattr(request.user, 'profile'):
            import pytz
            try:
                user_tz = pytz.timezone(request.user.profile.timezone)
                current_date = datetime.now(user_tz).date()
            except:
                current_date = datetime.now().date()
        else:
            current_date = datetime.now().date()
        
        # Format date as YYYY-MM-DD
        date_str = current_date.strftime('%Y-%m-%d')
        
        # Parse and sort tags for consistency
        tag_list = []
        if tags_param:
            tag_list = sorted([tag.strip().lower() for tag in tags_param.split(',')])
        
        # Create cache key
        tags_str = ','.join(tag_list) if tag_list else 'none'
        fast_str = str(fast_id) if fast_id else 'none'
        cache_key = f'patristic_quote_of_day:{date_str}:{fast_str}:{tags_str}:{lang}'
        
        # Try to get from cache first
        cached_quote = cache.get(cache_key)
        if cached_quote:
            return Response(cached_quote)
        
        # Build queryset with filters
        queryset = PatristicQuote.objects.prefetch_related('churches', 'fasts', 'tags')
        
        if fast_id:
            try:
                queryset = queryset.filter(fasts__id=int(fast_id))
            except ValueError:
                return Response(
                    {'detail': 'Invalid fast_id parameter.'},
                    status=400
                )
        
        if tag_list:
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        queryset = queryset.distinct().order_by('id')  # Consistent ordering for deterministic selection
        
        # Get count
        count = queryset.count()
        
        if count == 0:
            return Response(
                {
                    'detail': 'No patristic quotes found matching the specified criteria.',
                    'fast_id': fast_id,
                    'tags': tag_list if tag_list else None
                },
                status=404
            )
        
        # Create deterministic seed and hash it
        seed = f"{date_str}-{fast_str}-{tags_str}"
        hash_object = hashlib.md5(seed.encode())
        hash_int = int(hash_object.hexdigest(), 16)
        
        # Use modulo to select quote index
        selected_index = hash_int % count
        
        # Get the selected quote
        selected_quote = queryset[selected_index]
        
        # Serialize the quote
        serializer = PatristicQuoteSerializer(selected_quote, context={'request': request, 'lang': lang})
        response_data = serializer.data
        
        # Cache for 24 hours (86400 seconds)
        cache.set(cache_key, response_data, 86400)
        
        return Response(response_data)

