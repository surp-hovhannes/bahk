from rest_framework import generics, permissions
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Prefetch
from ..serializers import ProfileSerializer, ProfileImageSerializer
from ..models import Profile, Fast


class ProfileDetailView(generics.RetrieveUpdateAPIView):
    """
    API view to retrieve and update the authenticated user's profile.

    This view allows an authenticated user to retrieve their own profile information and update it as needed.
    The user's profile is identified based on the authenticated user making the request.

    Inherits:
        - RetrieveUpdateAPIView: A view that provides GET (retrieve) and PUT/PATCH (update) functionality.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Returns:
        - The profile data of the authenticated user.
    """
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        # Optimized: Use select_related and prefetch_related for better performance
        # This prevents N+1 queries when accessing user and church data
        return Profile.objects.select_related('user', 'church').prefetch_related(
            Prefetch('fasts', queryset=Fast.objects.select_related('church'))
        ).get(id=self.request.user.profile.id)


class ProfileImageUploadView(generics.UpdateAPIView):
    """
    API view to handle profile image uploads for the authenticated user.

    This view allows an authenticated user to upload or update their profile image. The image is parsed
    and processed using MultiPartParser and FormParser to handle file uploads.

    Inherits:
        - UpdateAPIView: A view that provides PUT/PATCH functionality for updating a model instance.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Parsers:
        - MultiPartParser: Parses multipart HTML form content, typically used for file uploads.
        - FormParser: Parses HTML form content.

    Returns:
        - The updated profile data with the new profile image.
    """
    serializer_class = ProfileImageSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_object(self):
        return self.request.user.profile
