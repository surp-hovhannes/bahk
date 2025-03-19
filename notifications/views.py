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
        token_value = request.data.get('token')
        
        # Validate request has necessary data
        if not token_value:
            return Response(
                {'error': 'Token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Check if token already exists
        existing_token = None
        try:
            existing_token = DeviceToken.objects.get(token=token_value)
        except DeviceToken.DoesNotExist:
            pass
            
        if existing_token:
            # Handle token update case - allow any user to update the token
            # This allows multiple accounts to use the same device (one at a time)
            logger.info(f"Token already exists, updating from user {existing_token.user} to {request.data.get('user')}")
            serializer = self.get_serializer(existing_token, data=request.data)
            operation = "update"
        else:
            # Handle token creation case
            serializer = self.get_serializer(data=request.data)
            operation = "create"
            
        # Validate the data through serializer
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Try to save the token
        try:
            if operation == "create":
                logger.info(f"Creating new device token with data: {serializer.validated_data}")
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                logger.info(f"Updating existing device token with data: {serializer.validated_data}")
                serializer.save()
                return Response(serializer.data)
        except ValidationError as e:
            # Handle model-level validation errors
            error_message = str(e)
            if isinstance(e.message_dict, dict):
                error_message = e.message_dict
            return Response(
                {'error': error_message},
                status=status.HTTP_400_BAD_REQUEST
            )

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
