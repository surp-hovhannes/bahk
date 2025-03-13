from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import DeviceToken
from .serializers import DeviceTokenSerializer
from .utils import send_push_notification
import logging
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# Create your views here.

class DeviceTokenCreateView(generics.CreateAPIView):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer

    def create(self, request, *args, **kwargs):
        logger.info(f"Received request data: {request.data}")
        # Try to get existing token
        try:
            instance = DeviceToken.objects.get(token=request.data.get('token'))
            serializer = self.get_serializer(instance, data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)
        except DeviceToken.DoesNotExist:
            # If it doesn't exist, create new one (with validation error handling)
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                self.perform_create(serializer)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except ValidationError as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )

    def perform_create(self, serializer):
        logger.info(f"Creating new device token with data: {serializer.validated_data}")
        serializer.save()

    def perform_update(self, serializer):
        logger.info(f"Updating existing device token with data: {serializer.validated_data}")
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
