"""Views for the icons app."""
import ipaddress
import logging
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from icons.cache import IconViewCache
from icons.models import Icon, IconFeedback
from icons.serializers import IconSerializer, IconFeedbackSerializer
from hub.services.icon_match_service import MatchLimits, match_icons
from hub.services.icon_matching import IconMatchRequest

class IsAdminOrReadOnly(BasePermission):
    """
    Custom permission: allow read access to anyone, write (POST/PUT/DELETE) to admins only.
    """
    def has_permission(self, request, view):
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True
        return request.user and request.user.is_staff

logger = logging.getLogger(__name__)


def anonymize_ip_address(raw_ip):
    """Anonymize a valid client IP address, or discard invalid/proxy-style values."""
    if not raw_ip:
        return None

    raw_ip = raw_ip.strip()
    if ',' in raw_ip:
        return None

    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        return None

    if addr.version == 4:
        network = ipaddress.ip_network(f"{addr}/24", strict=False)
        return str(network.network_address)

    masked = ipaddress.IPv6Address(
        int(addr) & 0xffff_ffff_ffff_0000_0000_0000_0000_0000
    )
    return str(masked)


class IconPagination(PageNumberPagination):
    """Larger page size for icon grid browsing."""
    page_size = 15
    page_size_query_param = 'page_size'
    max_page_size = 100


class IconListView(generics.ListCreateAPIView):
    """
    API endpoint that allows icons to be viewed, filtered, and uploaded.

    Permissions:
        - GET: Any user can view icons
        - POST: Admin users only

    Query Parameters (GET):
        - church: Filter by church ID
        - tags: Filter by tag name (can be comma-separated)
        - search: Search in title

    POST Body (multipart/form-data):
        - title: string (required)
        - church: integer (required) - church ID
        - image: file (required) - icon image file
        - tags: string (optional) - comma-separated tags

    Returns:
        A paginated list of icons with their details.

    Example Requests:
        GET /api/icons/
        GET /api/icons/?church=1
        GET /api/icons/?tags=cross,saint
        GET /api/icons/?search=nativity

        POST /api/icons/ (admin only, multipart/form-data)
        curl -X POST /api/icons/ -H "Authorization: Bearer TOKEN" \
          -F "title=St. Gregory" -F "church=1" -F "image=@icon.jpg" \
          -F "tags=patriarch,doctor"
    """
    serializer_class = IconSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = IconPagination

    def list(self, request, *args, **kwargs):
        """Return cached icon list responses for repeated GET requests."""
        cache_key = IconViewCache.list_key(request.query_params)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        response = super().list(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache.set(cache_key, response.data, IconViewCache.LIST_TTL)
        return response
    
    def get_queryset(self):
        """Get icons with optional filtering."""
        queryset = Icon.objects.select_related('church').prefetch_related('tags')
        
        # Filter by church
        church_id = self.request.query_params.get('church')
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        
        # Filter by tags
        tags = self.request.query_params.get('tags')
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        # Search in title
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
            )
        
        return queryset.distinct().order_by('-created_at')


class IconDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single icon to be viewed.
    
    Permissions:
        - GET: Any user can view icon details
    
    Returns:
        A JSON response with the icon details including:
        - id
        - title
        - church (id and name)
        - tags (list)
        - image S3 URL
        - thumbnail S3 URL
        - timestamps
    
    Example Requests:
        GET /api/icons/1/
    """
    serializer_class = IconSerializer
    permission_classes = [AllowAny]
    queryset = Icon.objects.select_related('church').prefetch_related('tags')

    def retrieve(self, request, *args, **kwargs):
        """Return cached icon detail responses for repeated GET requests."""
        cache_key = IconViewCache.detail_key(kwargs["pk"])
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        response = super().retrieve(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache.set(cache_key, response.data, IconViewCache.DETAIL_TTL)
        return response


class IconMatchView(views.APIView):
    """
    AI-powered icon matching endpoint.
    
    Uses LLM to analyze a natural language prompt and return the most
    appropriate icon(s) based on semantic understanding.
    
    Permissions:
        - POST: Any user can request icon matching
    
    Request Body:
        {
            "prompt": "string (required) - Natural language description",
            "church_id": "integer (optional) - Limit to specific church",
            "return_format": "string (optional) - 'id' or 'full' (default: 'full')",
            "max_results": "integer (optional) - Maximum icons to return (default: 3)"
        }
    
    Response:
        {
            "matches": [
                {
                    "icon_id": 1,
                    "confidence": "high|medium|low",
                    "icon": {...}  // Full icon details if return_format='full'
                }
            ]
        }
    
    Example Requests:
        POST /api/icons/match/
        {
            "prompt": "Icon showing the nativity scene",
            "return_format": "full"
        }
    """
    permission_classes = [AllowAny]
    
    def post(self, request):
        """Handle icon matching request."""
        # Extract request parameters
        prompt = request.data.get('prompt')
        church_id = request.data.get('church_id')
        return_format = request.data.get('return_format', 'full')
        raw_max_results = request.data.get('max_results', 3)
        
        # Validate prompt
        if not prompt:
            return Response(
                {'error': 'prompt is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate return_format
        if return_format not in ['id', 'full']:
            return Response(
                {'error': 'return_format must be "id" or "full"'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            if isinstance(raw_max_results, (bool, float)):
                raise ValueError
            max_results = int(raw_max_results)
        except (TypeError, ValueError):
            return Response(
                {'error': 'max_results must be an integer'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if max_results < 1 or max_results > 100:
            return Response(
                {'error': 'max_results must be between 1 and 100'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Normalize prompt (whitespace-only becomes empty string)
        prompt = prompt.strip() if isinstance(prompt, str) else str(prompt).strip()
        if not prompt:
            return Response(
                {'error': 'prompt is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(prompt.encode('utf-8')) > 12000:
            return Response({'error': 'prompt is too long'}, status=400)

        # Normalize church_id to int or None for stable cache keys
        if church_id is not None:
            try:
                if isinstance(church_id, (bool, float)):
                    raise ValueError
                church_id = int(church_id)
                if church_id < 1:
                    raise ValueError
            except (TypeError, ValueError):
                return Response(
                    {'error': 'church_id must be an integer'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        normalized_cache_data = {
            'prompt': prompt,
            'church_id': church_id or '',
            'return_format': return_format,
            'max_results': max_results,
        }
        cache_key = IconViewCache.match_key(normalized_cache_data)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data, status=status.HTTP_200_OK)
        
        # Get icons to match against
        queryset = Icon.objects.select_related('church').prefetch_related('tags')
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        
        icons = list(queryset)
        
        outcome = match_icons(icons, IconMatchRequest(
            kind='content', primary_text=prompt, max_results=max_results,
        ), limits=MatchLimits(total_seconds=20, call_seconds=20))
        icons_by_id = {icon.id: icon for icon in icons}
        matches = []
        for result in outcome.matches:
            icon = icons_by_id.get(result['id'])
            if icon is None:
                continue
            match_data = {**result, 'icon_id': icon.id}
            if return_format == 'full':
                match_data['icon'] = IconSerializer(icon).data
            matches.append(match_data)
        response_data = {**outcome.to_dict(), 'matches': matches}
        if outcome.status == 'complete':
            cache.set(cache_key, response_data, IconViewCache.MATCH_TTL)
        response_status = 503 if outcome.status == 'unavailable' and not matches else 200
        return Response(response_data, status=response_status)


class FeedbackAnonRateThrottle(AnonRateThrottle):
    """Scoped throttle for the icon feedback endpoint (20 requests/hour)."""
    rate = '20/hour'
    scope = 'feedback'


class IconFeedbackCreateView(views.APIView):
    """
    POST-only endpoint for submitting icon feedback.

    Accepts:
        - feedback_type: 'mislabel' | 'suggested_tags' | 'general'
        - description: text (10-2000 chars)
        - suggested_tags: comma-separated (required if type=suggested_tags)
        - submitter_email: optional valid email

    Snapshots icon title and tags at submission time.
    Anonymizes the submitter's IP (last octet zeroed).
    Returns 404 if icon does not exist.
    """
    permission_classes = [AllowAny]

    def get_throttles(self):
        """Conditionally apply scoped throttling based on settings."""
        if settings.ENABLE_FEEDBACK_THROTTLING:
            return [FeedbackAnonRateThrottle()]
        return []

    def post(self, request, pk):
        # 1. Resolve icon
        try:
            icon = Icon.objects.get(pk=pk)
        except Icon.DoesNotExist:
            return Response(
                {'error': 'Icon not found.'},
                status=status.HTTP_404_NOT_FOUND
            )

        # 2. Validate payload
        serializer = IconFeedbackSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 3. Snapshot icon state
        tags_string = ', '.join(tag.name for tag in icon.tags.all())

        # 4. Anonymize IP
        anonymized_ip = anonymize_ip_address(request.META.get('REMOTE_ADDR', ''))

        # 5. Create feedback record
        IconFeedback.objects.create(
            icon=icon,
            feedback_type=serializer.validated_data['feedback_type'],
            description=serializer.validated_data['description'],
            suggested_tags=serializer.validated_data.get('suggested_tags', ''),
            submitter_email=serializer.validated_data.get('submitter_email', ''),
            icon_title_at_time=icon.title,
            icon_tags_at_time=tags_string,
            http_user_agent=request.META.get('HTTP_USER_AGENT', ''),
            ip_address=anonymized_ip,
        )

        return Response(
            {'message': 'Thank you for your feedback!'},
            status=status.HTTP_201_CREATED
        )
