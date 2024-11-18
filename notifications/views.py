from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import DeviceToken
from .serializers import DeviceTokenSerializer
from .utils import send_push_notification
import logging

logger = logging.getLogger(__name__)

# Create your views here.

class DeviceTokenCreateView(generics.CreateAPIView):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer

    def create(self, request, *args, **kwargs):
        logger.info(f"Received request data: {request.data}")
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        logger.info(f"Saving device token with data: {serializer.validated_data}")
        serializer.save()

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
