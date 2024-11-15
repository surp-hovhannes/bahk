from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import DeviceToken
from .serializers import DeviceTokenSerializer
from .utils import send_push_notification

# Create your views here.

class DeviceTokenCreateView(generics.CreateAPIView):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer

class TestPushNotificationView(APIView):
    def post(self, request):
        token = request.data.get('token')
        message = request.data.get('message', 'Test notification')
        
        if not token:
            return Response(
                {'error': 'Token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        result = send_push_notification(
            message=message,
            tokens=[token]
        )
        
        return Response(result)
