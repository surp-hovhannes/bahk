from django.shortcuts import render
from rest_framework import generics
from .models import DeviceToken
from .serializers import DeviceTokenSerializer

# Create your views here.

class DeviceTokenCreateView(generics.CreateAPIView):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer
