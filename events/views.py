"""
API views for the events app.
Provides endpoints for retrieving events, analytics, and statistics.
"""

from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Event, EventType
from .serializers import (
    EventSerializer, EventListSerializer, EventTypeSerializer,
    EventStatsSerializer, UserEventStatsSerializer, FastEventStatsSerializer
)


class EventListView(generics.ListAPIView):
    """
    List events with filtering options.
    """
    serializer_class = EventListSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        queryset = Event.objects.select_related(
            'event_type', 'user', 'content_type'
        ).order_by('-timestamp')
        
        # Filter by user
        user_id = self.request.query_params.get('user', None)
        if user_id:
            try:
                queryset = queryset.filter(user_id=int(user_id))
            except (ValueError, TypeError):
                pass
        
        # Filter by event type
        event_type = self.request.query_params.get('event_type', None)
        if event_type:
            queryset = queryset.filter(event_type__code=event_type)
        
        # Filter by target type
        target_type = self.request.query_params.get('target_type', None)
        if target_type:
            try:
                app_label, model = target_type.split('.')
                queryset = queryset.filter(
                    content_type__app_label=app_label,
                    content_type__model=model
                )
            except ValueError:
                pass
        
        # Filter by target object
        target_id = self.request.query_params.get('target_id', None)
        if target_id:
            try:
                queryset = queryset.filter(object_id=int(target_id))
            except (ValueError, TypeError):
                pass
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date', None)
        end_date = self.request.query_params.get('end_date', None)
        
        if start_date:
            try:
                start_date = timezone.datetime.fromisoformat(start_date)
                queryset = queryset.filter(timestamp__gte=start_date)
            except ValueError:
                pass
        
        if end_date:
            try:
                end_date = timezone.datetime.fromisoformat(end_date)
                queryset = queryset.filter(timestamp__lte=end_date)
            except ValueError:
                pass
        
        return queryset


class EventDetailView(generics.RetrieveAPIView):
    """
    Retrieve a specific event.
    """
    queryset = Event.objects.select_related('event_type', 'user', 'content_type')
    serializer_class = EventSerializer
    permission_classes = [permissions.IsAuthenticated]


class MyEventsView(generics.ListAPIView):
    """
    List events for the current user.
    """
    serializer_class = EventListSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return Event.objects.filter(
            user=self.request.user
        ).select_related(
            'event_type', 'content_type'
        ).order_by('-timestamp')


class EventTypeListView(generics.ListAPIView):
    """
    List all event types.
    """
    queryset = EventType.objects.filter(is_active=True).order_by('category', 'name')
    serializer_class = EventTypeSerializer
    permission_classes = [permissions.IsAuthenticated]


class EventStatsView(APIView):
    """
    Get overall event statistics and analytics.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        now = timezone.now()
        
        # Calculate date ranges
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)
        last_30d = now - timedelta(days=30)
        
        # Basic counts
        total_events = Event.objects.count()
        events_last_24h = Event.objects.filter(timestamp__gte=last_24h).count()
        events_last_7d = Event.objects.filter(timestamp__gte=last_7d).count()
        events_last_30d = Event.objects.filter(timestamp__gte=last_30d).count()
        
        # Top event types
        top_event_types = list(Event.objects.values(
            'event_type__name', 'event_type__code', 'event_type__category'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10])
        
        # Events by day (last 30 days)
        events_by_day = {}
        for i in range(30):
            day = last_30d + timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            count = Event.objects.filter(
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            events_by_day[day.strftime('%Y-%m-%d')] = count
        
        # Fast join statistics
        fast_joins_30d = Event.objects.filter(
            event_type__code=EventType.USER_JOINED_FAST,
            timestamp__gte=last_30d
        ).count()
        
        fast_leaves_30d = Event.objects.filter(
            event_type__code=EventType.USER_LEFT_FAST,
            timestamp__gte=last_30d
        ).count()
        
        fast_join_stats = {
            'joins_last_30d': fast_joins_30d,
            'leaves_last_30d': fast_leaves_30d,
            'net_joins_30d': fast_joins_30d - fast_leaves_30d,
        }
        
        # Recent milestone events
        milestone_events = Event.objects.filter(
            event_type__code=EventType.FAST_PARTICIPANT_MILESTONE,
            timestamp__gte=last_30d
        ).select_related(
            'event_type', 'user', 'content_type'
        ).order_by('-timestamp')[:5]
        
        stats_data = {
            'total_events': total_events,
            'events_last_24h': events_last_24h,
            'events_last_7d': events_last_7d,
            'events_last_30d': events_last_30d,
            'top_event_types': top_event_types,
            'events_by_day': events_by_day,
            'fast_join_stats': fast_join_stats,
            'milestone_events': milestone_events,
        }
        
        serializer = EventStatsSerializer(stats_data)
        return Response(serializer.data)


class UserEventStatsView(APIView):
    """
    Get event statistics for a specific user.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, user_id=None):
        # Default to current user if no user_id provided
        if user_id is None:
            user_id = request.user.id
        
        # Check permissions - users can only see their own stats unless they're staff
        if user_id != request.user.id and not request.user.is_staff:
            return Response(
                {'error': 'Permission denied'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get user events
        user_events = Event.objects.filter(user=user)
        
        # Basic counts
        total_events = user_events.count()
        fasts_joined = user_events.filter(
            event_type__code=EventType.USER_JOINED_FAST
        ).count()
        fasts_left = user_events.filter(
            event_type__code=EventType.USER_LEFT_FAST
        ).count()
        
        # Recent events
        recent_events = user_events.select_related(
            'event_type', 'content_type'
        ).order_by('-timestamp')[:10]
        
        # Event types breakdown
        event_types_breakdown = dict(user_events.values(
            'event_type__name'
        ).annotate(
            count=Count('id')
        ).values_list('event_type__name', 'count'))
        
        stats_data = {
            'user_id': user.id,
            'username': user.username,
            'total_events': total_events,
            'fasts_joined': fasts_joined,
            'fasts_left': fasts_left,
            'net_fast_joins': fasts_joined - fasts_left,
            'recent_events': recent_events,
            'event_types_breakdown': event_types_breakdown,
        }
        
        serializer = UserEventStatsSerializer(stats_data)
        return Response(serializer.data)


class FastEventStatsView(APIView):
    """
    Get event statistics for a specific fast.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, fast_id):
        try:
            from hub.models import Fast
            fast = Fast.objects.get(id=fast_id)
        except Fast.DoesNotExist:
            return Response(
                {'error': 'Fast not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get fast-related events
        fast_events = Event.objects.filter(
            object_id=fast_id,
            content_type__app_label='hub',
            content_type__model='fast'
        )
        
        # Basic counts
        total_events = fast_events.count()
        current_participants = fast.profiles.count()
        
        total_joins = fast_events.filter(
            event_type__code=EventType.USER_JOINED_FAST
        ).count()
        
        total_leaves = fast_events.filter(
            event_type__code=EventType.USER_LEFT_FAST
        ).count()
        
        # Milestone events
        milestone_events = fast_events.filter(
            event_type__code=EventType.FAST_PARTICIPANT_MILESTONE
        ).select_related(
            'event_type', 'user', 'content_type'
        ).order_by('-timestamp')[:5]
        
        # Join timeline (last 30 days)
        now = timezone.now()
        last_30d = now - timedelta(days=30)
        
        join_timeline = {}
        for i in range(30):
            day = last_30d + timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            
            joins = fast_events.filter(
                event_type__code=EventType.USER_JOINED_FAST,
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            
            leaves = fast_events.filter(
                event_type__code=EventType.USER_LEFT_FAST,
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            
            join_timeline[day.strftime('%Y-%m-%d')] = {
                'joins': joins,
                'leaves': leaves,
                'net': joins - leaves
            }
        
        # Recent activity
        recent_activity = fast_events.select_related(
            'event_type', 'user', 'content_type'
        ).order_by('-timestamp')[:10]
        
        stats_data = {
            'fast_id': fast.id,
            'fast_name': fast.name,
            'total_events': total_events,
            'current_participants': current_participants,
            'total_joins': total_joins,
            'total_leaves': total_leaves,
            'net_joins': total_joins - total_leaves,
            'milestone_events': milestone_events,
            'join_timeline': join_timeline,
            'recent_activity': recent_activity,
        }
        
        serializer = FastEventStatsSerializer(stats_data)
        return Response(serializer.data)


@api_view(['POST'])
@permission_classes([permissions.IsAdminUser])
def trigger_milestone_check(request, fast_id):
    """
    Manually trigger a milestone check for a fast.
    Admin-only endpoint for testing or manual triggering.
    """
    try:
        from hub.models import Fast
        from .signals import check_and_track_participation_milestones
        
        fast = Fast.objects.get(id=fast_id)
        check_and_track_participation_milestones(fast)
        
        return Response({
            'message': f'Milestone check triggered for fast: {fast.name}',
            'current_participants': fast.profiles.count()
        })
        
    except Fast.DoesNotExist:
        return Response(
            {'error': 'Fast not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {'error': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
