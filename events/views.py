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
from django.utils.translation import activate, get_language_from_request

from .models import Event, EventType, UserActivityFeed
from .serializers import (
    EventSerializer, EventListSerializer, EventTypeSerializer,
    EventStatsSerializer, UserEventStatsSerializer, FastEventStatsSerializer,
    UserActivityFeedSerializer, UserActivityFeedSummarySerializer
)


class EventListView(generics.ListAPIView):
    """
    List events with filtering options.
    """
    serializer_class = EventListSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        # Activate language for _i18n fields
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
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
                # Support both date-only and ISO datetime; make timezone-aware
                if len(start_date) == 10 and start_date.count('-') == 2:
                    parsed = timezone.datetime.fromisoformat(start_date)
                else:
                    parsed = timezone.datetime.fromisoformat(start_date)
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                queryset = queryset.filter(timestamp__gte=parsed)
            except ValueError:
                pass
        
        if end_date:
            try:
                if len(end_date) == 10 and end_date.count('-') == 2:
                    parsed = timezone.datetime.fromisoformat(end_date)
                else:
                    parsed = timezone.datetime.fromisoformat(end_date)
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                queryset = queryset.filter(timestamp__lte=parsed)
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
    
    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


class MyEventsView(generics.ListAPIView):
    """
    List events for the current user.
    """
    serializer_class = EventListSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        lang = self.request.query_params.get('lang') or get_language_from_request(self.request) or 'en'
        activate(lang)
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
    
    def get(self, request, *args, **kwargs):
        lang = request.query_params.get('lang') or get_language_from_request(request) or 'en'
        activate(lang)
        return super().get(request, *args, **kwargs)


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


class TrackDevotionalViewedView(APIView):
    """
    Track when a user watches/opens a devotional. We do not record which items they check, only that it happened.
    POST body: { "devotional_id": number }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        devotional_id = request.data.get('devotional_id')
        if not devotional_id:
            return Response({"error": "devotional_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from hub.models import Devotional
            devotional = Devotional.objects.get(id=int(devotional_id))
        except (ValueError, Devotional.DoesNotExist):
            return Response({"error": "Invalid devotional_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            Event.create_event(
                event_type_code=EventType.DEVOTIONAL_VIEWED,
                user=request.user,
                target=devotional,
                title="Devotional viewed",
                data={
                    'devotional_id': devotional.id,
                    'fast_id': devotional.day.fast.id if devotional.day and devotional.day.fast else None,
                    'day': devotional.day.date.isoformat() if devotional.day else None,
                },
                request=request,
            )
            return Response({"status": "ok"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TrackPrayerSetViewedView(APIView):
    """
    Track when a user views/opens a prayer set. We do not record which prayers they read, only that it happened.
    POST body: { "prayer_set_id": number }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        prayer_set_id = request.data.get('prayer_set_id')
        if not prayer_set_id:
            return Response({"error": "prayer_set_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from prayers.models import PrayerSet
            prayer_set = PrayerSet.objects.get(id=int(prayer_set_id))
        except (ValueError, PrayerSet.DoesNotExist):
            return Response({"error": "Invalid prayer_set_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            Event.create_event(
                event_type_code=EventType.PRAYER_SET_VIEWED,
                user=request.user,
                target=prayer_set,
                title="Prayer set viewed",
                data={
                    'prayer_set_id': prayer_set.id,
                    'church_id': prayer_set.church.id if prayer_set.church else None,
                    'church_name': prayer_set.church.name if prayer_set.church else None,
                },
                request=request,
            )
            return Response({"status": "ok"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TrackChecklistUsedView(APIView):
    """
    Track a generic fasting checklist interaction without capturing specific items.
    POST body: { "fast_id": number (optional), "action": string (optional) }
    
    Examples:
    - Fast-specific checklist: {"fast_id": 123, "action": "morning_review"}
    - General checklist: {"action": "daily_reflection"}
    - Minimal tracking: {} (just tracks that checklist was used)
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        fast_id = request.data.get('fast_id')  # Now optional
        action = request.data.get('action')  # optional small descriptor
        
        fast = None
        target = None
        
        # If fast_id is provided, validate and use it
        if fast_id:
            try:
                from hub.models import Fast
                fast = Fast.objects.get(id=int(fast_id))
                target = fast
            except (ValueError, Fast.DoesNotExist):
                return Response({"error": "Invalid fast_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            Event.create_event(
                event_type_code=EventType.CHECKLIST_USED,
                user=request.user,
                target=target,  # Can be None now
                title="Checklist used",
                data={
                    'fast_id': fast.id if fast else None,
                    'fast_name': fast.name if fast else None,
                    'action': action,
                    'context': 'fast_specific' if fast else 'general',
                },
                request=request,
            )
            
            # Invalidate the stats cache for this user since checklist_uses count changed
            from hub.utils import invalidate_fast_stats_cache
            invalidate_fast_stats_cache(request.user)
            
            return Response({"status": "ok"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TrackPrayerViewedView(APIView):
    """
    Track when a user views/opens an individual prayer.
    POST body: { "prayer_id": number }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        prayer_id = request.data.get('prayer_id')
        if not prayer_id:
            return Response({"error": "prayer_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from prayers.models import Prayer
            prayer = Prayer.objects.select_related('church', 'fast').get(id=int(prayer_id))
        except (ValueError, Prayer.DoesNotExist):
            return Response({"error": "Invalid prayer_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            Event.create_event(
                event_type_code=EventType.PRAYER_VIEWED,
                user=request.user,
                target=prayer,
                title="Prayer viewed",
                data={
                    "prayer_id": prayer.id,
                    "church_id": prayer.church_id,
                    "fast_id": prayer.fast_id,
                    "category": prayer.category,
                    "title": prayer.title,
                },
                request=request,
            )
            return Response({"status": "ok"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TrackPrayerRequestViewedView(APIView):
    """
    Track when a user views/opens an individual prayer request.
    POST body: { "prayer_request_id": number }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        prayer_request_id = request.data.get('prayer_request_id')
        if not prayer_request_id:
            return Response({"error": "prayer_request_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from prayers.models import PrayerRequest
            prayer_request = PrayerRequest.objects.get(id=int(prayer_request_id))
        except (ValueError, PrayerRequest.DoesNotExist):
            return Response({"error": "Invalid prayer_request_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            Event.create_event(
                event_type_code=EventType.PRAYER_REQUEST_VIEWED,
                user=request.user,
                target=prayer_request,
                title=f'Prayer request viewed: {prayer_request.title}',
                data={
                    "prayer_request_id": prayer_request.id,
                    "status": prayer_request.status,
                    "title": prayer_request.title,
                },
                request=request,
            )
            return Response({"status": "ok"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([permissions.IsAdminUser])
def trigger_milestone_check(request, fast_id):
    """
    Manually trigger milestone check for a fast (admin only).
    """
    try:
        from hub.models import Fast
        fast = Fast.objects.get(id=fast_id)
        
        # Import the milestone tracking function
        from .signals import check_and_track_participation_milestones
        
        # Trigger milestone check
        milestones_created = check_and_track_participation_milestones(fast)
        
        return Response({
            'message': f'Milestone check completed for {fast.name}',
            'milestones_created': milestones_created
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


class UserActivityFeedView(generics.ListAPIView):
    """
    Get user's activity feed with filtering and pagination.
    """
    serializer_class = UserActivityFeedSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        
        queryset = UserActivityFeed.objects.filter(
            user=user
        ).select_related(
            'event', 'event__event_type', 'content_type'
        ).order_by('-created_at')
        
        # Filter by activity type
        activity_type = self.request.query_params.get('activity_type', None)
        if activity_type:
            queryset = queryset.filter(activity_type=activity_type)
        
        # Filter by read status
        is_read = self.request.query_params.get('is_read', None)
        if is_read is not None:
            is_read_bool = is_read.lower() == 'true'
            queryset = queryset.filter(is_read=is_read_bool)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date', None)
        end_date = self.request.query_params.get('end_date', None)
        
        if start_date:
            try:
                parsed = timezone.datetime.fromisoformat(start_date)
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                queryset = queryset.filter(created_at__gte=parsed)
            except ValueError:
                pass
        
        if end_date:
            try:
                parsed = timezone.datetime.fromisoformat(end_date)
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                queryset = queryset.filter(created_at__lte=parsed)
            except ValueError:
                pass
        
        return queryset


class UserActivityFeedSummaryView(APIView):
    """
    Get summary statistics for user's activity feed.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = request.user
        
        # Get basic counts
        total_items = UserActivityFeed.objects.filter(user=user).count()
        unread_count = UserActivityFeed.objects.filter(user=user, is_read=False).count()
        read_count = total_items - unread_count
        
        # Get activity type breakdown
        activity_types = {}
        type_counts = UserActivityFeed.objects.filter(user=user).values(
            'activity_type'
        ).annotate(count=Count('id'))
        
        for item in type_counts:
            activity_types[item['activity_type']] = item['count']
        
        # Get recent activity (last 5 items)
        recent_activity = UserActivityFeed.objects.filter(
            user=user
        ).select_related(
            'event', 'event__event_type', 'content_type'
        ).order_by('-created_at')[:5]
        
        data = {
            'total_items': total_items,
            'unread_count': unread_count,
            'read_count': read_count,
            'activity_types': activity_types,
            'recent_activity': UserActivityFeedSerializer(recent_activity, many=True).data
        }
        
        return Response(data)


class UserMilestonesView(APIView):
    """
    Get user's milestones from activity feed.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = request.user
        
        # Get all milestone activity feed items
        milestone_items = UserActivityFeed.objects.filter(
            user=user,
            activity_type='milestone'
        ).select_related('content_type').order_by('-created_at')
        
        # Get milestone statistics
        total_milestones = milestone_items.count()
        unread_milestones = milestone_items.filter(is_read=False).count()
        
        # Group by milestone type from data field
        milestone_types = {}
        for item in milestone_items:
            milestone_type = item.data.get('milestone_type', 'unknown')
            if milestone_type not in milestone_types:
                milestone_types[milestone_type] = {
                    'count': 0,
                    'latest': None
                }
            milestone_types[milestone_type]['count'] += 1
            if milestone_types[milestone_type]['latest'] is None:
                milestone_types[milestone_type]['latest'] = item
        
        # Serialize milestone data
        milestone_data = []
        for milestone_type, info in milestone_types.items():
            milestone_data.append({
                'milestone_type': milestone_type,
                'count': info['count'],
                'latest_achievement': UserActivityFeedSerializer(info['latest']).data
            })
        
        data = {
            'total_milestones': total_milestones,
            'unread_milestones': unread_milestones,
            'milestone_types': milestone_data,
            'all_milestones': UserActivityFeedSerializer(milestone_items, many=True).data
        }
        
        return Response(data)


class MarkActivityReadView(APIView):
    """
    Mark activity feed items as read.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        user = request.user
        activity_ids = request.data.get('activity_ids', [])
        mark_all = request.data.get('mark_all', False)
        
        if mark_all:
            # Mark all unread items as read
            updated_count = UserActivityFeed.objects.filter(
                user=user, is_read=False
            ).update(
                is_read=True, 
                read_at=timezone.now()
            )
            
            return Response({
                'message': f'Marked {updated_count} items as read',
                'updated_count': updated_count
            })
        
        elif activity_ids:
            # Mark specific items as read
            if not isinstance(activity_ids, list):
                return Response(
                    {'error': 'activity_ids must be a list'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            updated_count = UserActivityFeed.objects.filter(
                user=user, 
                id__in=activity_ids
            ).update(
                is_read=True, 
                read_at=timezone.now()
            )
            
            return Response({
                'message': f'Marked {updated_count} items as read',
                'updated_count': updated_count
            })
        
        else:
            return Response(
                {'error': 'Either activity_ids or mark_all must be provided'}, 
                status=status.HTTP_400_BAD_REQUEST
            )


class GenerateActivityFeedView(APIView):
    """
    Generate activity feed items for a user (admin only).
    This can be used to populate the feed with historical data or test data.
    """
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request):
        user_id = request.data.get('user_id')
        days_back = request.data.get('days_back', 30)
        
        try:
            days_back = int(days_back)
        except (TypeError, ValueError):
            days_back = 30

        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Calculate date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days_back)
        
        # Get user's events in the date range
        events = Event.objects.filter(
            user=user,
            timestamp__gte=start_date,
            timestamp__lte=end_date
        ).select_related('event_type', 'content_type')
        
        # Create feed items from events
        created_count = 0
        for event in events:
            feed_item = UserActivityFeed.create_from_event(event, user)
            if feed_item:
                created_count += 1
        
        return Response({
            'message': f'Generated {created_count} activity feed items for user {user.username}',
            'created_count': created_count,
            'date_range': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            }
        })
