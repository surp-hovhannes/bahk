from notifications.utils import telemetry_task_kwargs
import logging
from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.db import IntegrityError, transaction
from rest_framework import generics, mixins, permissions, serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from .utils import _device_token_log_id
from .receipts import note_registration
from .models import DeviceToken, PostFastEncouragementEmail
from .serializers import DeviceTokenSerializer, PostFastEncouragementEmailSerializer
from .tasks import send_push_notification_task
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class PostFastEncouragementEmailViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = PostFastEncouragementEmailSerializer
    queryset = PostFastEncouragementEmail.objects.select_related('fast').order_by('pk')

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list' and 'fast' in self.request.query_params:
            try:
                fast_id = serializers.IntegerField(min_value=1).run_validation(
                    self.request.query_params['fast']
                )
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({'fast': exc.detail}) from exc
            queryset = queryset.filter(fast_id=fast_id)
        return queryset

    def perform_create(self, serializer):
        self.save_copy(serializer)

    def perform_update(self, serializer):
        self.save_copy(serializer)

    def save_copy(self, serializer):
        try:
            with transaction.atomic():
                serializer.save()
        except IntegrityError as exc:
            # A competing write can claim the fast after serializer validation.
            fast = serializer.validated_data.get('fast')
            instance_id = serializer.instance.pk if serializer.instance else None
            if fast and self.queryset.filter(fast=fast).exclude(pk=instance_id).exists():
                raise serializers.ValidationError(
                    {'fast': [serializer.duplicate_fast_message]}
                ) from exc
            raise


# Create your views here.

class DeviceTokenCreateView(generics.CreateAPIView):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        token_value = request.data.get('token')
        logger.info(
            'Received device token registration request',
            extra={
                'device_type': request.data.get('device_type'),
                'has_token': bool(token_value),
                'user_id': request.user.id,
            }
        )
        
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
            old_user_id = existing_token.user.id if existing_token.user else None
            new_user_id = request.user.id
            
            if old_user_id != new_user_id:
                logger.info(
                    'Device token ownership changed',
                    extra={
                        'device_token_id': _device_token_log_id(token_value),
                        'old_user_id': old_user_id,
                        'new_user_id': new_user_id
                    }
                )
            
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
                self.perform_create(serializer)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                serializer.save(user=request.user, is_active=True)
                # Late DNR receipts for sends before this registration must not undo it.
                note_registration(existing_token.pk)
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
        send_push_notification_task.delay(
            message="Test notification",
            user_ids=[request.user.id],
            **telemetry_task_kwargs('test'),
        )
        return Response({"message": "Test notification sent"}, status=status.HTTP_200_OK)

def unsubscribe(request):
    """Handle unsubscribe requests"""
    token = request.GET.get('token')
    if not token:
        return redirect('notifications:unsubscribe_error')

    User = get_user_model()

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
