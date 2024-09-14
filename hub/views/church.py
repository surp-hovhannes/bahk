from rest_framework import generics, permissions
from ..serializers import ChurchSerializer
from ..models import Church


class ChurchListView(generics.ListAPIView):
    """
    API view to retrieve all churches.

    This view allows an authenticated user to retrieve all churches.

    Inherits:
        - ListAPIView: A view that provides GET functionality to retrieve a list of model instances.

    Permissions:
        - AllowAny: Anyone can access this view.

    Returns:
        - All churches in JSON format.

    Responses:
        - 200 OK: Successfully retrieved all churches.
    """
    serializer_class = ChurchSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return Church.objects.all()

class ChurchDetailView(generics.RetrieveAPIView):
    """
    API view to retrieve the church's information.

    This view allows an authenticated user to retrieve the church's information.

    Inherits:
        - RetrieveAPIView: A view that provides GET functionality to retrieve a model instance.

    Permissions:
        - AllowAny: Anyone can access this view.

    URL Parameters:
        - pk (optional): The ID of the church which is being retrieved. No ID, returns a list of all churches.

    Returns:
        - The church's information in JSON format.

    Responses:
        - 200 OK: Successfully retrieved the church's information.
        - 404 Not Found: Church with the specified ID does not exist.
    """
    serializer_class = ChurchSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Church.objects.all()
