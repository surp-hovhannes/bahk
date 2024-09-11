from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth.models import User, Group
from rest_framework import viewsets, permissions
from hub import serializers
from ..forms import CustomUserCreationForm
from ..models import Profile, Church



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

class UserRegistrationView(APIView):
    permission_classes = []  # Allow any user to register

    def post(self, request):
        form = CustomUserCreationForm(request.data)
        if form.is_valid():
            user = form.save()
            church_name = form.cleaned_data["church"]
            Profile.objects.get_or_create(
                user=user, 
                church=Church.objects.get(name=church_name)
            )
            return Response({"detail": "User registered successfully"}, status=status.HTTP_201_CREATED)
        return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
