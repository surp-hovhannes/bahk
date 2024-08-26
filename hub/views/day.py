from rest_framework import generics, permissions
from ..models import Day
from ..serializers import DaySerializer

class FastDaysListView(generics.ListAPIView):
    """
    API view to list all days associated with a specific fast.

    This view allows an authenticated user to retrieve a list of all days associated with a particular fast,
    identified by its ID in the URL.

    Inherits:
        - ListAPIView: A view that provides GET functionality to list model instances.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    URL Parameters:
        - fast_id: The ID of the fast for which to retrieve the associated days.

    Returns:
        - A list of days associated with the specified fast.
    """
    serializer_class = DaySerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        fast_id = self.kwargs.get('fast_id')
        return Day.objects.filter(fast__id=fast_id)

class UserDaysView(generics.ListAPIView):
    """
    API view to list all days associated with the user's active fasts.

    This view allows an authenticated user to retrieve a list of all days associated with the fasts
    that they have joined.

    Inherits:
        - ListAPIView: A view that provides GET functionality to list model instances.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Returns:
        - A list of days associated with the fasts that the authenticated user has joined.
    """
    serializer_class = DaySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user_fasts = self.request.user.profile.fasts.all()
        return Day.objects.filter(fast__in=user_fasts)
