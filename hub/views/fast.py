from rest_framework import generics, permissions
from django.utils.translation import activate, get_language_from_request
from ..constants import NUMBER_PARTICIPANTS_TO_SHOW_WEB
from ..models import Fast, Church, Day, Profile, FastParticipantMap
from ..serializers import FastSerializer, JoinFastSerializer, ParticipantSerializer, FastStatsSerializer, DaySerializer, FastParticipantMapSerializer
from .mixins import ChurchContextMixin, TimezoneMixin
from django.utils import timezone
from rest_framework.exceptions import ValidationError
import datetime
from rest_framework import views, response, status
import logging
import pytz
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers, vary_on_cookie
from django.conf import settings
from django.core.cache import cache
from django.utils.encoding import force_str
from django.shortcuts import get_object_or_404
from django.db.models import Q, Count, Min, Max, Sum, Prefetch
from rest_framework.pagination import LimitOffsetPagination
from ..utils import invalidate_fast_participants_cache
from functools import wraps
from hub.tasks import generate_participant_map
import sentry_sdk


CACHE_TTL = getattr(settings, 'CACHE_MIDDLEWARE_SECONDS', 60 * 15)  # 15 minutes default

def get_cache_key(prefix, *args):
    """Generate a cache key with the given prefix and arguments."""
    return f"bahk:{prefix}:{'_'.join(force_str(arg) for arg in args)}"

class FastListView(ChurchContextMixin, TimezoneMixin, generics.ListAPIView):
    """
    API view to list all fasts for a specific church within a configurable date range.

    This view inherits from `ChurchContextMixin`, which determines the church based on the user's profile 
    if authenticated, or a `church_id` query parameter if unauthenticated. The fasts are filtered based on the 
    determined church and a date range (i.e., a fast is included if *any* of its days fall within the date range).

    Inherits:
        - ChurchContextMixin: Provides the church context for filtering.
        - TimezoneMixin: Provides the timezone context for serializers.
        - ListAPIView: Standard DRF view for listing model instances.

    Permissions:
        - AllowAny: Any user, authenticated or not, can access this view.
    
    Query Parameters:
        - start_date: Optional. Start date in YYYY-MM-DD format. Defaults to 6 months ago.
        - end_date: Optional. End date in YYYY-MM-DD format. Defaults to 6 months in future.
        - tz: Optional. Timezone offset from UTC in the IANA format (e.g., America/New_York).
    
    Returns:
        - A list of fasts filtered by:
            - church context
            - date range (defaults to ±6 months from current date if no parameters provided)
        - Results are distinct and unpaginated
    """
    serializer_class = FastSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_church_participant_count(self, church_id):
        """Get the total number of participants across all fasts for this church."""
        count_key = f'church_{church_id}_participant_count'
        count = cache.get(count_key)
        
        if count is None:
            # Calculate the sum of participants across all fasts for this church
            count = Fast.objects.filter(
                church_id=church_id
            ).annotate(
                participant_count=Count('profiles', distinct=True)
            ).aggregate(
                total=Sum('participant_count')
            )['total'] or 0
            
            # Cache this count for 10 minutes
            cache.set(count_key, count, timeout=600)
            
        return count

    def get_cache_key(self, church_id, start_date, end_date, tz):
        """Generate a cache key that includes the participant count."""
        # Get current participant count
        participant_count = self.get_church_participant_count(church_id)
        
        # Include the count in the cache key
        return f'fast_list_qs:{church_id}:{start_date}:{end_date}:{tz}:{participant_count}'

    def get_queryset(self):
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
        church = self.get_church()
        tz = self.get_timezone()
        today = timezone.localdate(timezone=tz)
        
        # Default date range (±6 months)
        default_start = today - datetime.timedelta(days=180)
        default_end = today + datetime.timedelta(days=180)

        # Parse date strings properly
        start_date = default_start
        end_date = default_end

        start_date_str = self.request.query_params.get('start_date')
        end_date_str = self.request.query_params.get('end_date')

        if start_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if end_date_str:
            try:
                end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        # Generate cache key
        cache_key = self.get_cache_key(
            church.id,
            start_date.isoformat(),
            end_date.isoformat(),
            str(tz)
        )

        # Try to get from cache
        cached_queryset = cache.get(cache_key)
        if cached_queryset is not None:
            return cached_queryset

        # If not in cache, generate queryset
        queryset = Fast.objects.annotate(
            participant_count=Count('profiles', distinct=True),
            total_days=Count('days', distinct=True),
            start_date=Min('days__date'),
            end_date=Max('days__date'),
            current_day_count=Count(
                'days',
                filter=Q(days__date__lte=today),
                distinct=True
            )
        ).filter(
            church=church,
            days__date__gte=start_date,
            days__date__lte=end_date
        ).select_related(
            'church'
        ).prefetch_related(
            'days',
            'profiles'
        ).distinct()

        # Cache the queryset for 10 minutes
        cache.set(cache_key, queryset, timeout=600)  # 10 minutes
        return queryset

    def invalidate_cache(self, church_id):
        """Invalidate all cached lists for a given church."""
        # Invalidate the participant count cache
        cache.delete(f'church_{church_id}_participant_count')
        
        # Then find and delete any queryset caches
        pattern = f'fast_list_qs:{church_id}:*'
        if hasattr(cache, 'keys'):
            # Redis backend supports pattern matching
            keys = cache.keys(pattern)
            if keys:
                cache.delete_many(keys)
        else:
            # LocMemCache doesn't support pattern matching, so we'll clear all cache
            # This is less efficient but works for testing
            cache.clear()


@method_decorator(vary_on_headers('Authorization'), name='dispatch')
class FastDetailView(TimezoneMixin, generics.RetrieveAPIView):
    """
    API view to retrieve detailed information about a specific fast.

    This view allows authenticated users to retrieve the details of a specific fast by its ID.
    The response is cached for 15 minutes to improve performance, with separate cache entries
    for authenticated and unauthenticated users to prevent data leakage.

    Cache keys are generated based on:
    - Fast ID
    - User authentication status
    - Timezone
    
    Cache invalidation should occur when:
    - The fast is updated
    - A user joins/leaves the fast
    - The fast's church information changes

    Inherits:
        - TimezoneMixin: Provides the timezone context for serializers.
        - RetrieveAPIView: Standard DRF view for retrieving a single model instance by ID.

    Permissions:
        - AllowAny: Any user can access this view.

    Query Parameters:
        - tz: Optional. Timezone offset from UTC in the IANA format (e.g., America/New_York).
    
    Returns:
        - Details of the requested fast.
    """
    serializer_class = FastSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return Fast.objects.all().select_related('church').prefetch_related('days', 'profiles')

    def get_object(self):
        # Return the model instance as expected by DRF
        return super().get_object()

    def retrieve(self, request, *args, **kwargs):
        # Generate cache key using the fast ID, auth status, and timezone
        is_authenticated = request.user.is_authenticated
        cache_key = get_cache_key(
            'fast_detail',
            self.kwargs['pk'],
            'auth' if is_authenticated else 'anon',
            self.get_timezone().zone
        )
        
        # Try to get serialized data from cache
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return response.Response(cached_data)
        
        # If not in cache, get the response from the parent class
        response_data = super().retrieve(request, *args, **kwargs)
        
        # Cache the serialized data
        cache.set(cache_key, response_data.data, CACHE_TTL)
        
        return response_data


@method_decorator(cache_page(CACHE_TTL), name='dispatch')
@method_decorator(vary_on_headers('Authorization'), name='dispatch')
class FastByDateView(ChurchContextMixin, TimezoneMixin, generics.ListAPIView):
    """
    API view to list fasts based on a specific date or the current date.

    This view inherits from `ChurchContextMixin`, which determines the church context. The fasts are filtered 
    by the church and the specified date. If no date is provided, the current date is used.

    Inherits:
        - ChurchContextMixin: Provides the church context for filtering.
        - TimezoneMixin: Provides the timezone context for serializers.
        - ListAPIView: Standard DRF view for listing model instances.

    Permissions:
        - AllowAny: Any user, authenticated or not, can access this view.

    Query Parameters:
        - date: Optional. A string representing the date in `yyyy-mm-dd` format.
        - tz: Optional. A string representing the timezone offset from UTC in the IANA format (e.g., America/New_York).
        - church_id: Optional. A string representing the church id. Required if unauthenticated.

    Returns:
        - A list of fasts filtered by the church and date context.
    """
    serializer_class = FastSerializer
    permission_classes = [permissions.AllowAny]  # Allow any user to access this view

    def get_queryset(self):
        church = self.get_church()
        date_str = self.request.query_params.get('date')
        tz = self.get_timezone()
        
        # Generate cache key
        cache_key = get_cache_key('fast_by_date', church.id, date_str, tz.zone)
        
        # Try to get serialized data from cache
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            # If found in cache, filter the queryset by the cached IDs
            return Fast.objects.filter(id__in=cached_data)

        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: yyyy-mm-dd.")
        else:
            target_date = timezone.localdate(timezone=tz)

        # Optimize queryset with select_related and prefetch_related
        queryset = Fast.objects.annotate(
            participant_count=Count('profiles', distinct=True),
            total_days=Count('days', distinct=True),
            start_date=Min('days__date'),
            end_date=Max('days__date'),
            current_day_count=Count(
                'days',
                filter=Q(days__date__lte=target_date),
                distinct=True
            )
        ).filter(
            church=church,
            days__date=target_date
        ).select_related(
            'church'
        ).prefetch_related(
            'days',
            'profiles'
        )
        
        # Cache the list of Fast IDs instead of the queryset
        fast_ids = list(queryset.values_list('id', flat=True))
        cache.set(cache_key, fast_ids, CACHE_TTL)
        
        return queryset


class JoinFastView(generics.UpdateAPIView):
    """
    API view for a user to join a specific fast.

    This view allows an authenticated user to join a fast by adding the fast to their profile.
    The fast is specified by its ID in the request data.

    Inherits:
        - UpdateAPIView: Standard DRF view for updating a model instance.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Request Data:
        - fast_id: The ID of the fast to join.

    Returns:
        - The updated user profile with the new fast added.
    """
    serializer_class = JoinFastSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user.profile

    def perform_update(self, serializer):
        fast = Fast.objects.get(id=self.request.data.get('fast_id'))

        if not fast:
            return response.Response({"detail": "Fast not found."}, status=status.HTTP_404_NOT_FOUND)
        
        if fast in self.get_object().fasts.all():
            return response.Response({"detail": "You are already part of this fast."}, status=status.HTTP_400_BAD_REQUEST)
        
        self.get_object().fasts.add(fast)
        serializer.save()
        
        # Invalidate the participant list cache for this fast
        invalidate_fast_participants_cache(fast.id)


class LeaveFastView(generics.UpdateAPIView):
    """
    API view for a user to leave a specific fast.

    This view allows an authenticated user to leave a fast by removing the fast from their profile.
    The fast is specified by its ID in the request data.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Request Data:
        - fast_id: The ID of the fast to leave.

    Returns:
        - A success message or an error message if the user is not part of the fast.
    """
    serializer_class = JoinFastSerializer  # You can reuse the JoinFastSerializer if it works for this
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user.profile

    def perform_update(self, serializer):
        fast_id = self.request.data.get('fast_id')
        fast = Fast.objects.filter(id=fast_id).first()

        if not fast:
            return response.Response({"detail": "Fast not found."}, status=status.HTTP_404_NOT_FOUND)

        if fast not in self.get_object().fasts.all():
            return response.Response({"detail": "You are not part of this fast."}, status=status.HTTP_400_BAD_REQUEST)

        self.get_object().fasts.remove(fast)
        
        # Invalidate the participant list cache for this fast
        invalidate_fast_participants_cache(fast_id)
        
        return response.Response({"detail": "Successfully left the fast."}, status=status.HTTP_200_OK)


def vary_on_query_params(*params):
    """
    Decorator that varies the cache based on specific query parameters.
    This ensures different cache entries for different parameter values.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(self, request, *args, **kwargs):
            # Extract query params and add them to QUERY_STRING 
            # for cache middleware to use in cache key
            if request and hasattr(request, 'query_params'):
                query_params = []
                for param in params:
                    value = request.query_params.get(param)
                    if value:
                        query_params.append(f"{param}={value}")
                
                # Create a custom query string for the cache key
                query_string = "&".join(query_params)
                request.META['QUERY_STRING'] = query_string
            
            # Make sure to pass all arguments through correctly
            return view_func(self, request, *args, **kwargs)
        return _wrapped_view
    return decorator


class FastParticipantsView(views.APIView):
    """
    API view to retrieve participants of a specific fast.

    This view returns a list of up to 6 participants in the fast identified by the `fast_id` provided in the URL.
    It includes participant profile data such as username, profile image, and location.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.
    
    URL Parameters:
        - fast_id: The ID of the fast for which to retrieve the participants.
        - limit: Optional. A number to limit the number of participants to return. Defaults to 6.

    Returns:
        - A list of participant profiles for the specified fast.
    """
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(cache_page(60 * 10))  # Cache for 10 minutes
    @method_decorator(vary_on_headers('Authorization'))
    @vary_on_query_params('limit') 
    def get(self, request, fast_id):
        # Try to get fast from cache first
        cache_key = get_cache_key('fast_participants_simple_view', fast_id)
        fast = cache.get(cache_key)
        
        if not fast:
            # If not in cache, get from database and cache it
            fast = get_object_or_404(Fast, id=fast_id)
            cache.set(cache_key, fast, CACHE_TTL)

        limit = request.query_params.get('limit', NUMBER_PARTICIPANTS_TO_SHOW_WEB)
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = NUMBER_PARTICIPANTS_TO_SHOW_WEB

        # Optimized query with select_related and prefetch_related
        other_participants = fast.profiles.select_related(
            'user'  # For email/username
        ).order_by('user__date_joined')[:limit]
            
        serialized_participants = ParticipantSerializer(
            other_participants, 
            many=True, 
            context={'request': request}
        )
        return response.Response(serialized_participants.data)


@method_decorator(cache_page(60 * 10), name='dispatch')  # Cache for 10 minutes
@method_decorator(vary_on_headers('Authorization'), name='dispatch')
class PaginatedFastParticipantsView(generics.ListAPIView):
    """
    API view to retrieve paginated participants of a specific fast.

    This view returns a paginated list of participants in the fast identified by the `fast_id` 
    provided in the URL. It includes participant profile data such as username, profile image, 
    and location. Results are paginated using LimitOffsetPagination.

    Inherits:
        - ListAPIView: A DRF view that provides GET functionality with built-in pagination.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.
    
    URL Parameters:
        - fast_id: The ID of the fast for which to retrieve the participants.
        - limit: Optional. Number of results to return per page. Defaults to settings.PAGE_SIZE (10).
        - offset: Optional. The initial index from which to return the results. Defaults to 0.

    Returns:
        - A paginated response with:
            - count: Total number of participants 
            - next: URL to next page (if applicable)
            - previous: URL to previous page (if applicable)
            - results: List of participant profiles for the current page
    """
    serializer_class = ParticipantSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = LimitOffsetPagination
    
    def get_queryset(self):
        """
        Optimized queryset that:
        1. Uses get_object_or_404 for cleaner 404 handling
        2. Prefetches related fields needed by serializer
        3. Orders results consistently
        4. Caches the fast lookup
        """
        fast_id = self.kwargs.get('fast_id')
        
        # Try to get fast from cache first
        cache_key = get_cache_key('fast_participants_view', fast_id)
        fast = cache.get(cache_key)
        
        if not fast:
            # If not in cache, get from database and cache it
            fast = get_object_or_404(Fast, id=fast_id)
            cache.set(cache_key, fast, CACHE_TTL)
        
        return fast.profiles.select_related(
            'user'  # For email/username
        ).order_by('user__date_joined')  # Consistent ordering
    
    def get_serializer_context(self):
        """Add request to serializer context for thumbnail URL generation"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def paginate_queryset(self, queryset):
        """Override to add count caching for pagination performance"""
        fast_id = self.kwargs.get('fast_id')
        count_cache_key = get_cache_key('fast_participants_count', fast_id)
        
        # Check if we have a cached count
        cached_count = cache.get(count_cache_key)
        
        if cached_count is not None and hasattr(self.paginator, 'count'):
            # If count is cached, use it directly to avoid COUNT(*) query
            self.paginator.count = cached_count
            return super().paginate_queryset(queryset)
        
        # Get paginated results normally (will perform COUNT(*))
        result = super().paginate_queryset(queryset)
        
        # Cache the count for future requests if it was calculated
        if hasattr(self.paginator, 'count'):
            cache.set(count_cache_key, self.paginator.count, CACHE_TTL)
            
        return result


class FastStatsView(views.APIView):
    """
    API view to retrieve statistics about users fasting participation

    This view returns statistics about the specified user's fasting participation.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Returns:
        - Array of fast ids that the user has joined
        - Total number of fasts the user has joined
        - Total number of fast days the user has participated in
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Optimized: Prefetch related data to avoid N+1 queries
        # This ensures that when the serializer calls obj.fasts.aggregate(),
        # it has efficient access to the related data
        user_profile = request.user.profile
        
        # Prefetch the fasts and their days to optimize the serializer queries
        optimized_profile = Profile.objects.select_related('user', 'church').prefetch_related(
            Prefetch('fasts', queryset=Fast.objects.prefetch_related('days'))
        ).get(id=user_profile.id)
        
        serialized_stats = FastStatsSerializer(optimized_profile)
        return response.Response(serialized_stats.data)


class FastOnDate(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        if request.user.is_authenticated:
            fast = _get_fast_for_user_on_date(request)
            return response.Response(FastSerializer(fast).data)
        else:
            fast = _get_fast_on_date(request)
            return response.Response(FastSerializer(fast).data)


class FastOnDateWithoutUser(views.APIView):
    """Returns fast data on the date specified in query params (`?date=<yyyymmdd>`).
    
    If no date provided, defaults to today. If there is no fast on the given date, or the date string provided is
    invalid or malformed, the response will contain no fast information.
    """

    def get(self, request):
        fast = _get_fast_on_date(request)
        return response.Response(FastSerializer(fast).data)
    

def _get_fast_for_user_on_date(request):
    """Returns the fast that the user is participating in on a given day."""
    user = request.user
    date_str = request.query_params.get("date")
    if date_str is None:
        # get today by default
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)

    return _get_user_fast_on_date(user, date)


def _get_user_fast_on_date(user, date):
    """Given user, gets fast that the user is participating in on a given day."""
    # there should not be multiple fasts per day, but in case of bug, return last created
    return Fast.objects.filter(
        profiles__user=user, 
        days__date=date, 
        church=user.profile.church
    ).select_related('church').last()


def _get_fast_on_date(request):
    """Returns the fast for the user's church on a given day whether the user is participating in it or not."""
    date_str = request.query_params.get("date")
    if date_str is None:
        # get today by default
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)

    church_id = request.query_params.get("church_id")
    if church_id is None:
        church = request.user.profile.church
    else:
        church = Church.objects.get(id=church_id)

    # Filter the fasts based on the church
    # there should not be multiple fasts per day, but in case of bug, return last created
    return Fast.objects.filter(
        church=church, 
        days__date=date
    ).select_related('church').last()


def _parse_date_str(date_str):
    """Parses a date string in the format yyyymmdd into a date object."""
    try:
        date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError as e:
        logging.error("Date string %s did not follow the expected format yyyymmdd. Returning None.", date_str)
        return None

    return date


class FastParticipantsMapView(views.APIView):
    """
    API view to retrieve the participants map for a specific fast.

    This view returns the URL to the most recent generated map for the fast identified by the `fast_id` in the URL.
    If no map exists or if the map is older than a day, it triggers asynchronous generation of a new map.
    
    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.
    
    URL Parameters:
        - fast_id: The ID of the fast for which to retrieve the map.
        
    Query Parameters:
        - force_update: If set to 'true', forces regeneration of the map regardless of age.

    Returns:
        - Map metadata including URL, last updated timestamp, participant count, and format.
        - If no map exists yet, returns a 202 Accepted response indicating a map is being generated.
    """
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(cache_page(60 * 60))  # Cache for 1 hour
    @method_decorator(vary_on_headers('Authorization'))
    def get(self, request, fast_id):
        try:
            # Set context for Sentry errors
            sentry_sdk.set_context("request_info", {
                "fast_id": fast_id,
                "user_id": request.user.id,
                "church_id": request.user.profile.church_id if hasattr(request.user, 'profile') else None
            })
            
            # Add breadcrumb for better debugging
            sentry_sdk.add_breadcrumb(
                category="api",
                message=f"Fetching participants map for fast {fast_id}",
                level="info"
            )
            
            # Check if force_update is requested
            force_update = request.query_params.get('force_update', '').lower() == 'true'
            
            # If force_update is requested, bypass the cache
            if force_update:
                return self._get_map(request, fast_id, force_update=True)
                
            return self._get_map(request, fast_id)
        except Exception as e:
            # Capture the exception with additional context
            sentry_sdk.capture_exception(e)
            
            # Log the error for local debugging
            logging.error(f"Error retrieving participant map: {str(e)}")
            
            # Re-raise for Django's standard error handling
            raise
    
    def _get_map(self, request, fast_id, force_update=False):
        """Internal method to get or generate the map."""
        # Use a custom Sentry transaction for performance monitoring
        with sentry_sdk.start_transaction(op="api.get_map", name=f"Get Fast Map {fast_id}"):
            current_fast = Fast.objects.filter(id=fast_id).first()

            if not current_fast:
                return response.Response({"detail": "Fast not found."}, status=404)
                
            # Check if a map exists for this fast
            with sentry_sdk.start_span(op="db.query", description="Check for existing map"):
                map_obj = FastParticipantMap.objects.filter(fast=current_fast).order_by('-last_updated').first()
            
            # If no map exists, if it's older than a day, or if force_update is requested, generate a new one
            if not map_obj or (timezone.now() - map_obj.last_updated).days >= 1 or force_update:
                # Add context about map regeneration decision
                sentry_sdk.set_context("map_generation", {
                    "reason": "not_exists" if not map_obj else "outdated" if (timezone.now() - map_obj.last_updated).days >= 1 else "force_update",
                    "map_age_days": (timezone.now() - map_obj.last_updated).days if map_obj else None,
                    "force_update": force_update
                })
                
                # Trigger async task to generate the map
                with sentry_sdk.start_span(op="task.queue", description="Queue map generation task"):
                    generate_participant_map.delay(fast_id)
                
                if not map_obj:
                    # If no map exists yet, return a 202 Accepted response
                    return response.Response(
                        {"detail": "Map is being generated. Please try again in a few minutes."},
                        status=202
                    )
                elif force_update:
                    # If force_update was requested, inform the user
                    return response.Response(
                        {
                            "detail": "Map update has been triggered. The current map is being returned, but a new one is being generated.",
                            "map": FastParticipantMapSerializer(map_obj).data
                        }
                    )
            
            # Return the map data
            serializer = FastParticipantMapSerializer(map_obj)
            return response.Response(serializer.data)
