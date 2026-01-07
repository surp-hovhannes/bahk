"""Views for prayers app."""
from django.db.models import Q
from django.utils.translation import activate, get_language_from_request
from rest_framework import generics
from rest_framework.permissions import AllowAny

from learning_resources.cache import BookmarkCacheManager
from prayers.models import Prayer, PrayerSet
from prayers.serializers import (
    PrayerSerializer,
    PrayerSetSerializer,
    PrayerSetListSerializer
)


class PrayerListView(generics.ListAPIView):
    """
    API endpoint that allows prayers to be viewed.

    Permissions:
        - GET: Any user can view prayers
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter prayers by matching text in title or text content.
                       Case-insensitive partial matches are supported.
        - church (int): Optional. Filter prayers by church ID.
        - category (str): Optional. Filter prayers by category (morning, evening, meal, etc.).
        - tags (str): Optional. Filter prayers by tag name(s). Comma-separated for multiple tags.
        - fast (int): Optional. Filter prayers by fast ID.

    Returns:
        A JSON response with paginated prayer results.

    Example Requests:
        GET /api/prayers/
        GET /api/prayers/?search=lord
        GET /api/prayers/?church=1
        GET /api/prayers/?category=morning
        GET /api/prayers/?tags=daily,thanksgiving
        GET /api/prayers/?fast=1
    """
    serializer_class = PrayerSerializer
    permission_classes = [AllowAny]
    
    def get_serializer_context(self):
        """Add bookmark cache data to context for performance optimization."""
        context = super().get_serializer_context()
        context['use_bookmark_cache'] = True
        return context
    
    def get_queryset(self):
        """Get filtered and ordered queryset of prayers."""
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = Prayer.objects.select_related('church', 'fast').prefetch_related('tags')
        
        # Apply search filter if provided
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(text__icontains=search)
            )
        
        # Filter by church
        church_id = self.request.query_params.get('church', None)
        if church_id:
            try:
                queryset = queryset.filter(church_id=int(church_id))
            except ValueError:
                return Prayer.objects.none()
        
        # Filter by category
        category = self.request.query_params.get('category', None)
        if category:
            queryset = queryset.filter(category=category)
        
        # Filter by tags (supports multiple tags separated by comma)
        tags = self.request.query_params.get('tags', None)
        if tags:
            tag_list = [tag.strip() for tag in tags.split(',')]
            for tag in tag_list:
                queryset = queryset.filter(tags__name__iexact=tag)
        
        # Filter by fast
        fast_id = self.request.query_params.get('fast', None)
        if fast_id:
            try:
                queryset = queryset.filter(fast_id=int(fast_id))
            except ValueError:
                return Prayer.objects.none()
        
        return queryset.distinct().order_by('-created_at')


class PrayerDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single prayer to be viewed.

    Permissions:
        - GET: Any user can view prayer details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the prayer details including translations.

    Example Requests:
        GET /api/prayers/1/
        GET /api/prayers/1/?lang=hy
    """
    serializer_class = PrayerSerializer
    permission_classes = [AllowAny]
    queryset = Prayer.objects.select_related('church', 'fast').prefetch_related('tags')


class PrayerSetListView(generics.ListAPIView):
    """
    API endpoint that allows prayer sets to be viewed.

    Permissions:
        - GET: Any user can view prayer sets
        - POST/PUT/PATCH/DELETE: Not supported

    Query Parameters:
        - search (str): Optional. Filter prayer sets by matching text in title or description.
                       Case-insensitive partial matches are supported.
        - church (int): Optional. Filter prayer sets by church ID.
        - category (str): Optional. Filter prayer sets by category (morning, evening, general).

    Returns:
        A JSON response with paginated prayer set results (without full prayer details).

    Example Requests:
        GET /api/prayer-sets/
        GET /api/prayer-sets/?search=morning
        GET /api/prayer-sets/?church=1
        GET /api/prayer-sets/?category=morning
    """
    serializer_class = PrayerSetListSerializer
    permission_classes = [AllowAny]
    
    def get_serializer_context(self):
        """Add bookmark cache data to context for performance optimization."""
        context = super().get_serializer_context()
        context['use_bookmark_cache'] = True
        return context
    
    def get_queryset(self):
        """Get filtered and ordered queryset of prayer sets."""
        # Activate requested language for _i18n virtual fields
        lang = self.request.query_params.get('lang') or get_language_from_request(
            self.request
        ) or 'en'
        activate(lang)
        
        queryset = PrayerSet.objects.select_related('church')
        
        # Apply search filter if provided
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search)
            )
        
        # Filter by church
        church_id = self.request.query_params.get('church', None)
        if church_id:
            try:
                queryset = queryset.filter(church_id=int(church_id))
            except ValueError:
                return PrayerSet.objects.none()
        
        # Filter by category
        category = self.request.query_params.get('category', None)
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset.order_by('-created_at')


class PrayerSetDetailView(generics.RetrieveAPIView):
    """
    API endpoint that allows a single prayer set to be viewed with all prayers.

    Permissions:
        - GET: Any user can view prayer set details
        - POST/PUT/PATCH/DELETE: Not supported

    Returns:
        A JSON response with the prayer set details including all ordered prayers.

    Example Requests:
        GET /api/prayer-sets/1/
        GET /api/prayer-sets/1/?lang=hy
    """
    serializer_class = PrayerSetSerializer
    permission_classes = [AllowAny]
    
    def get_serializer_context(self):
        """Add bookmark cache data to context for performance optimization."""
        context = super().get_serializer_context()
        context['use_bookmark_cache'] = True
        return context
    
    def get_queryset(self):
        """Optimize queryset with prefetch for prayers."""
        return PrayerSet.objects.select_related('church').prefetch_related(
            'memberships__prayer__church',
            'memberships__prayer__fast',
            'memberships__prayer__tags'
        )

# Prayer Request Views

from datetime import date
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from events.models import Event, EventType, UserActivityFeed, UserMilestone
from hub.utils import get_user_profile_safe
from prayers.models import PrayerRequest, PrayerRequestAcceptance, PrayerRequestPrayerLog
from prayers.serializers import (
    PrayerRequestSerializer,
    PrayerRequestCreateSerializer,
    PrayerRequestUpdateSerializer,
    PrayerRequestAcceptanceSerializer,
    PrayerRequestPrayerLogSerializer,
    PrayerRequestThanksSerializer,
)
from prayers.tasks import moderate_prayer_request_task


class PrayerRequestViewSet(viewsets.ModelViewSet):
    """
    API endpoint for prayer requests.

    list: Get all approved, active prayer requests
    create: Create a new prayer request (triggers moderation)
    retrieve: Get a specific prayer request
    update: Update prayer request (only if pending_moderation and owner)
    destroy: Soft delete prayer request (owner only)
    accept: Accept a prayer request to pray for it
    accepted: Get all prayer requests the user has accepted
    mark_prayed: Mark that you prayed for a request today
    send_thanks: Send thanks message to all who accepted (requester only, if completed)

    Query Parameters (for list action):
        - status (str): Optional. Filter by status. Can be a single status or comma-separated
                       multiple statuses. Valid values: pending_moderation, approved, rejected,
                       completed, deleted, active. Special value 'active' means approved and not expired.
                       Default: approved (active, non-expired only).
        - mine (bool): Optional. Filter to show only the current user's own prayer requests.
                       Use ?mine=true or ?mine=1. When used, status filter still applies.

    Example Requests:
        GET /api/prayer-requests/
        GET /api/prayer-requests/?status=completed
        GET /api/prayer-requests/?status=pending_moderation,completed
        GET /api/prayer-requests/?mine=true
        GET /api/prayer-requests/?mine=true&status=approved
        GET /api/prayer-requests/?mine=true&status=active
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Get prayer requests based on action."""
        if self.action == 'list':
            queryset = PrayerRequest.objects.select_related('requester')
            
            # Filter by mine parameter (user's own requests)
            mine_param = self.request.query_params.get('mine', None)
            is_mine_filter = mine_param and mine_param.lower() in ('true', '1', 'yes')
            if is_mine_filter:
                queryset = queryset.filter(requester=self.request.user)
            
            # Filter by status if provided
            status_param = self.request.query_params.get('status', None)
            if status_param:
                # Support comma-separated multiple statuses
                status_list = [s.strip() for s in status_param.split(',')]
                # Build Q objects for OR logic when combining multiple statuses
                q_objects = []
                has_active = 'active' in status_list
                
                if has_active:
                    # 'active' means approved and not expired
                    q_objects.append(
                        Q(status='approved', expiration_date__gt=timezone.now())
                    )
                    # Remove 'active' from list and process other statuses
                    status_list = [s for s in status_list if s != 'active']
                
                # Validate and add other status values
                valid_statuses = ['pending_moderation', 'approved', 'rejected', 'completed', 'deleted']
                valid_status_list = [s for s in status_list if s in valid_statuses]
                
                if valid_status_list:
                    q_objects.append(Q(status__in=valid_status_list))
                
                # Apply filters with OR logic if we have any Q objects
                if q_objects:
                    # Combine all Q objects with OR logic
                    combined_q = q_objects[0]
                    for q_obj in q_objects[1:]:
                        combined_q |= q_obj
                    queryset = queryset.filter(combined_q)
                elif not has_active:
                    # Invalid status values (and not 'active'), return empty queryset
                    return PrayerRequest.objects.none()
            elif not is_mine_filter:
                # Default behavior: only approved, non-expired requests
                # (skip default when mine filter is active - show all user's requests)
                queryset = PrayerRequest.objects.get_active_approved().select_related('requester')
            
            return queryset
        elif self.action == 'accepted':
            # Get user's accepted requests
            return PrayerRequest.objects.filter(
                acceptances__user=self.request.user
            ).select_related('requester').distinct()
        else:
            # For retrieve/update/destroy, include all statuses
            # Permissions will be checked separately
            return PrayerRequest.objects.select_related('requester')

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'create':
            return PrayerRequestCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return PrayerRequestUpdateSerializer
        elif self.action == 'send_thanks':
            return PrayerRequestThanksSerializer
        return PrayerRequestSerializer

    def get_object(self):
        """Retrieve object and ensure expired approved requests are completed."""
        prayer_request = super().get_object()
        return self._ensure_completed_if_expired(prayer_request)

    def _ensure_completed_if_expired(self, prayer_request):
        """
        When a request is expired but still approved, mark it completed
        and create completion side-effects if they don't already exist.
        """
        prayer_request, _ = prayer_request.complete_if_expired_with_side_effects()
        return prayer_request

    def perform_create(self, serializer):
        """Create prayer request and trigger moderation."""
        prayer_request = serializer.save()

        # Trigger moderation task asynchronously
        moderate_prayer_request_task.delay(prayer_request.id)

    def perform_update(self, serializer):
        """Only allow updates if user is owner and status is pending_moderation."""
        instance = self.get_object()

        if instance.requester != self.request.user:
            raise PermissionDenied('You can only edit your own prayer requests.')

        if instance.status != 'pending_moderation':
            raise ValidationError('Prayer requests can only be edited while pending moderation.')

        serializer.save()

    def perform_destroy(self, instance):
        """Soft delete: set status to deleted."""
        if instance.requester != self.request.user:
            raise PermissionDenied('You can only delete your own prayer requests.')

        instance.status = 'deleted'
        instance.save(update_fields=['status', 'updated_at'])

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        """
        Accept a prayer request to commit to praying for it.
        
        POST /api/prayer-requests/{id}/accept/
        """
        prayer_request = self.get_object()

        # Prevent users from manually accepting their own request via API
        # (automatic acceptance happens during moderation)
        if prayer_request.requester == request.user:
            return Response(
                {'detail': 'You cannot manually accept your own prayer request. Your request is automatically accepted when approved.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate prayer request is approved and not expired
        if prayer_request.status != 'approved':
            return Response(
                {'detail': 'This prayer request is not available for acceptance.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if prayer_request.is_expired():
            return Response(
                {'detail': 'This prayer request has expired.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if already accepted
        if PrayerRequestAcceptance.objects.filter(
            prayer_request=prayer_request,
            user=request.user
        ).exists():
            return Response(
                {'detail': 'You have already accepted this prayer request.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create acceptance
        acceptance = PrayerRequestAcceptance.objects.create(
            prayer_request=prayer_request,
            user=request.user,
            counts_for_milestones=True
        )

        # Create event
        Event.create_event(
            event_type_code=EventType.PRAYER_REQUEST_ACCEPTED,
            user=request.user,
            target=prayer_request,
            title=f'Accepted prayer request: {prayer_request.title}',
            data={
                'prayer_request_id': prayer_request.id,
                'acceptance_id': acceptance.id,
            }
        )

        # Check for acceptance milestones (only for OTHER people's requests, not own)
        # Count acceptances where user is NOT the requester
        other_acceptances_count = PrayerRequestAcceptance.objects.filter(
            user=request.user,
            counts_for_milestones=True
        ).exclude(
            prayer_request__requester=request.user
        ).count()

        if other_acceptances_count == 1:
            UserMilestone.create_milestone(
                user=request.user,
                milestone_type='first_prayer_request_accepted',
                related_object=prayer_request,
                data={
                    'prayer_request_id': prayer_request.id,
                    'title': prayer_request.title,
                }
            )
        # Check for prayer warrior milestones
        elif other_acceptances_count == 10:
            UserMilestone.create_milestone(
                user=request.user,
                milestone_type='prayer_warrior_10',
                data={'total_accepted': 10}
            )
        elif other_acceptances_count == 50:
            UserMilestone.create_milestone(
                user=request.user,
                milestone_type='prayer_warrior_50',
                data={'total_accepted': 50}
            )

        # Create activity feed item for requester
        if not prayer_request.is_anonymous:
            # Get display name from profile, fallback to "User {first_letter}" for privacy
            profile = get_user_profile_safe(request.user)
            if profile and profile.name:
                acceptor_name = profile.name
            else:
                # Privacy-safe fallback: "User D" instead of showing email
                first_letter = request.user.email[0].upper() if request.user.email else 'U'
                acceptor_name = f'User {first_letter}'
            
            UserActivityFeed.objects.create(
                user=prayer_request.requester,
                activity_type='prayer_request_accepted',
                title=f'{acceptor_name} accepted your prayer request',
                description=f'Someone is now praying for your request "{prayer_request.title}".',
                target=prayer_request,
                data={
                    'prayer_request_id': prayer_request.id,
                    'accepted_by_user_id': request.user.id,
                }
            )

        serializer = PrayerRequestAcceptanceSerializer(acceptance, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def accepted(self, request):
        """
        Get all prayer requests the user has accepted.
        
        GET /api/prayer-requests/accepted/
        """
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='mark-prayed')
    def mark_prayed(self, request, pk=None):
        """
        Mark that you prayed for this request today.
        
        POST /api/prayer-requests/{id}/mark-prayed/
        """
        prayer_request = self.get_object()

        # Validate user has accepted this request
        if not PrayerRequestAcceptance.objects.filter(
            prayer_request=prayer_request,
            user=request.user
        ).exists():
            return Response(
                {'detail': 'You must accept this prayer request before marking it as prayed.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if already logged today
        today = timezone.now().date()
        log, created = PrayerRequestPrayerLog.objects.get_or_create(
            prayer_request=prayer_request,
            user=request.user,
            prayed_on_date=today
        )

        if not created:
            return Response(
                {'detail': 'You have already marked this prayer for today.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check for faithful intercessor milestone (7 consecutive days)
        # Get last 7 days of prayer logs for this user
        from datetime import timedelta
        last_7_days = [today - timedelta(days=i) for i in range(7)]
        logs_last_7_days = PrayerRequestPrayerLog.objects.filter(
            user=request.user,
            prayed_on_date__in=last_7_days
        ).values_list('prayed_on_date', flat=True).distinct()

        if len(logs_last_7_days) == 7:
            # User has prayed for 7 consecutive days
            UserMilestone.create_milestone(
                user=request.user,
                milestone_type='faithful_intercessor',
                data={
                    'consecutive_days': 7,
                    'last_date': today.isoformat(),
                }
            )

        serializer = PrayerRequestPrayerLogSerializer(log, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='send-thanks')
    def send_thanks(self, request, pk=None):
        """
        Send a thank you message to all who accepted your prayer request.
        Only available to requester after prayer request is completed.
        
        POST /api/prayer-requests/{id}/send-thanks/
        Body: {"message": "Thank you all for your prayers!"}
        """
        prayer_request = self.get_object()

        # Validate requester
        if prayer_request.requester != request.user:
            return Response(
                {'detail': 'Only the requester can send a thank you message.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Validate status is completed
        if prayer_request.status != 'completed':
            return Response(
                {'detail': 'Thank you messages can only be sent for completed prayer requests.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate and get message
        serializer = PrayerRequestThanksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.validated_data['message']

        # Get all users who accepted this request (excluding requester's auto-acceptance)
        acceptances = prayer_request.acceptances.select_related('user').exclude(
            user=prayer_request.requester
        )

        if not acceptances.exists():
            return Response(
                {'detail': 'No one accepted this prayer request.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        recipient_count = acceptances.count()

        # Create event
        Event.create_event(
            event_type_code=EventType.PRAYER_REQUEST_THANKS_SENT,
            user=request.user,
            target=prayer_request,
            title=f'Sent thanks for prayer request: {prayer_request.title}',
            data={
                'prayer_request_id': prayer_request.id,
                'message': message,
                'recipient_count': recipient_count,
            }
        )

        # Create activity feed items for all who accepted (excluding requester)
        # Get display name from profile, fallback to "User {first_letter}" for privacy
        profile = get_user_profile_safe(request.user)
        if profile and profile.name:
            sender_name = profile.name
        else:
            # Privacy-safe fallback: "User D" instead of showing email
            first_letter = request.user.email[0].upper() if request.user.email else 'U'
            sender_name = f'User {first_letter}'
        
        for acceptance in acceptances:
            UserActivityFeed.objects.create(
                user=acceptance.user,
                activity_type='prayer_request_thanks',
                title=f'Thank you from {sender_name}',
                description=message,
                target=prayer_request,
                data={
                    'prayer_request_id': prayer_request.id,
                    'from_user_id': request.user.id,
                }
            )

        return Response({
            'detail': f'Thank you message sent to {recipient_count} people.',
            'recipient_count': recipient_count
        }, status=status.HTTP_200_OK)
