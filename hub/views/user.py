from django.contrib.auth.models import User, Group
from rest_framework import viewsets, permissions
from hub import serializers


class UserViewSet(viewsets.ModelViewSet):
    """API endpoint that allows user to be viewed or edited."""
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = serializers.UserSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]


class GroupViewSet(viewsets.ModelViewSet):
    """API endpoint that allows groups to be viewed or edited."""
    queryset = Group.objects.all()
    serializer_class = serializers.GroupSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
