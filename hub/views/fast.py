from rest_framework import generics, permissions
from ..constants import NUMBER_PARTICIPANTS_TO_SHOW_WEB
from ..models import Fast, Church
from ..serializers import FastSerializer, JoinFastSerializer, ParticipantSerializer, FastStatsSerializer
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
from django.db.models import Q
from rest_framework.pagination import LimitOffsetPagination
from ..utils import invalidate_fast_participants_cache
from functools import wraps

CACHE_TTL = getattr(settings, 'CACHE_MIDDLEWARE_SECONDS', 60 * 15)  # 15 minutes default

def get_cache_key(prefix, *args):
    """Generate a cache key with the given prefix and arguments."""
    return f"bahk:{prefix}:{'_'.join(force_str(arg) for arg in args)}"

@method_decorator(cache_page(CACHE_TTL), name='dispatch')
@method_decorator(vary_on_headers('Authorization'), name='dispatch')
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

    def get_queryset(self):
        church = self.get_church()
        tz = self.get_timezone()
        today = timezone.localdate(timezone=tz)
        
        # Default date range (±6 months)
        default_start = today - datetime.timedelta(days=180)
        default_end = today + datetime.timedelta(days=180)

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

        # Optimize queryset with select_related and prefetch_related
        return Fast.objects.filter(
            church=church,
            days__date__gte=start_date,
            days__date__lte=end_date
        ).select_related(
            'church'
        ).prefetch_related(
            'days',
            'profiles'
        ).distinct()


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
        queryset = Fast.objects.filter(
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


# Function to vary cache based on query parameters
def vary_on_query_params(*params):
    """
    Decorator that varies the cache based on specific query parameters.
    This ensures different cache entries for different parameter values.
    """
    def decorator(func):
        @wraps(func)
        def inner(self, request, *args, **kwargs):
            query_params = []
            for param in params:
                value = request.query_params.get(param)
                if value:
                    query_params.append(f"{param}={value}")
            
            # Create a custom query string for the cache key
            query_string = "&".join(query_params)
            request.META['QUERY_STRING'] = query_string
            
            return func(self, request, *args, **kwargs)
        return inner
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
        current_fast = Fast.objects.filter(id=fast_id).first()

        if not current_fast:
            return response.Response({"detail": "Fast not found."}, status=404)

        limit = request.query_params.get('limit', NUMBER_PARTICIPANTS_TO_SHOW_WEB)
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = NUMBER_PARTICIPANTS_TO_SHOW_WEB

        other_participants = current_fast.profiles.all()[:limit]            
        serialized_participants = ParticipantSerializer(other_participants, many=True, context={'request': request})
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
        fast_id = self.kwargs.get('fast_id')
        current_fast = Fast.objects.filter(id=fast_id).first()
        
        if not current_fast:
            return []
            
        return current_fast.profiles.all()
        
    def list(self, request, *args, **kwargs):
        # Check if the fast exists first
        fast_id = self.kwargs.get('fast_id')
        current_fast = Fast.objects.filter(id=fast_id).first()
        
        if not current_fast:
            return response.Response({"detail": "Fast not found."}, status=404)
            
        # Continue with standard list behavior if fast exists
        return super().list(request, *args, **kwargs)


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
        user_profile = request.user.profile
        serialized_stats = FastStatsSerializer(user_profile)
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
