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

        # Get and validate date parameters
        start_date = default_start
        end_date = default_end

        start_date_str = self.request.query_params.get('start_date')
        end_date_str = self.request.query_params.get('end_date')

        if start_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass  # Keep default if invalid

        if end_date_str:
            try:
                end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass  # Keep default if invalid

        return Fast.objects.filter(
            church=church,
            days__date__gte=start_date,
            days__date__lte=end_date
        ).distinct()


class FastDetailView(TimezoneMixin, generics.RetrieveAPIView):
    """
    API view to retrieve detailed information about a specific fast.

    This view allows authenticated users to retrieve the details of a specific fast by its ID.

    Inherits:
        - TimezoneMixin: Provides the timezone context for serializers.
        - RetrieveAPIView: Standard DRF view for retrieving a single model instance by ID.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Query Parameters:
        - tz: Optional. Timezone offset from UTC in the IANA format (e.g., America/New_York).
    
    Returns:
        - Details of the requested fast.
    """
    serializer_class = FastSerializer
    permission_classes = [permissions.AllowAny]

    queryset = Fast.objects.all()


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

        if date_str:
            try:
                # Parse the date string (expected format: yyyy-mm-dd)
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError("Invalid date format. Expected format: yyyy-mm-dd.")
        else:
            # Default to the current date
            target_date = timezone.localdate(timezone=tz)

        return Fast.objects.filter(church=church, days__date=target_date)


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
        return response.Response({"detail": "Successfully left the fast."}, status=status.HTTP_200_OK)


class FastParticipantsView(views.APIView):
    """
    API view to retrieve participants of a specific fast.

    This view returns a list of up to 6 participants in the fast identified by the `fast_id` provided in the URL.
    It includes participant profile data such as username, profile image, and location.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.
    
    URL Parameters:
        - fast_id: The ID of the fast for which to retrieve the participants.

    Returns:
        - A list of participant profiles for the specified fast.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, fast_id):
        # Retrieve the fast based on the fast_id provided in the URL
        current_fast = Fast.objects.filter(id=fast_id).first()

        if not current_fast:
            return response.Response({"detail": "Fast not found."}, status=404)

        # Retrieve up to 6 participants for the specified fast
        other_participants = current_fast.profiles.all()[:NUMBER_PARTICIPANTS_TO_SHOW_WEB]

        # Serialize the participant data
        serialized_participants = ParticipantSerializer(other_participants, many=True, context={'request': request})

        return response.Response(serialized_participants.data)


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
        # Retrieve the user's profile
        user_profile = request.user.profile

        # Serialize the user's fasting statistics
        serialized_stats = FastStatsSerializer(user_profile)

        return response.Response(serialized_stats.data)



# legacy Fast views

class FastOnDate(views.APIView):
    """Returns fast data on the date specified in query params (`?date=<yyyymmdd>`) for the user.
    
    If no date provided, defaults to today. If there is no fast on the given date, or the date string provided is
    invalid or malformed, the response will contain no fast information.
    """
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
    return Fast.objects.filter(profiles__user=user, days__date=date, church=user.profile.church).last()


def _get_fast_on_date(request):
    """Returns the fast for the user's church on a given day whether the user is participating in it or not.
    check for params in the requet that define church and date"""

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
    return Fast.objects.filter(church=church, days__date=date).last()


def _parse_date_str(date_str):
    """Parses a date string in the format yyyymmdd into a date object.
    
    Args:
        date_str (str): string to parse into a date. Expects the format yyyymmdd (e.g., 20240331 for March 31, 2024).

    Returns:
        datetime.date: date object corresponding to the date string. None if the date is invalid.
    """
    try:
        date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError as e:
        logging.error("Date string %s did not follow the expected format yyyymmdd. Returning None.", date_str)
        return None

    return date
