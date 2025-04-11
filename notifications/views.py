import logging
from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.models import User
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.http import HttpResponseBadRequest
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import DeviceToken
from .serializers import DeviceTokenSerializer
from .utils import send_push_notification
from .tasks import send_push_notification_task
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# Create your views here.

class DeviceTokenCreateView(generics.CreateAPIView):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

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
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        send_push_notification_task.delay(request.user.id)
        return Response({"message": "Test notification sent"}, status=status.HTTP_200_OK)

def unsubscribe(request):
    """Handle unsubscribe requests"""
    token = request.GET.get('token')
    if not token:
        return redirect('notifications:unsubscribe_error')
    
    try:
        signer = TimestampSigner()
        user_id = signer.unsign(token, max_age=60*60*24*7)  # 7 days
        user = User.objects.get(id=user_id)
        
        # Update profile setting
        user.profile.receive_promotional_emails = False
        user.profile.save()
        
        return redirect('notifications:unsubscribe_success')
        
    except (BadSignature, SignatureExpired, User.DoesNotExist):
        return redirect('notifications:unsubscribe_error')

def unsubscribe_success(request):
    """Show success page after unsubscribing"""
    return render(request, 'notifications/unsubscribe_success.html')

def unsubscribe_error(request):
    """Show error page if unsubscribe fails"""
    return render(request, 'notifications/unsubscribe_error.html')
