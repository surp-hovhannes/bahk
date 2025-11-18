"""
Admin interface for the events app.
Provides comprehensive views for events, event types, and analytics.
"""

from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q, Avg, FloatField
from django.db.models.functions import Cast
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils import timezone
from datetime import timedelta
import json
import csv

from .models import Event, EventType, UserActivityFeed, UserMilestone, Announcement


@admin.register(EventType)
class EventTypeAdmin(admin.ModelAdmin):
    """
    Admin interface for EventType model.
    """
    list_display = [
        'name', 'code', 'category', 'is_active', 
        'track_in_analytics', 'requires_target', 'event_count'
    ]
    list_filter = ['category', 'is_active', 'track_in_analytics', 'requires_target']
    search_fields = ['name', 'code', 'description']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'code', 'description')
        }),
        ('Configuration', {
            'fields': ('category', 'is_active', 'track_in_analytics', 'requires_target')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def event_count(self, obj):
        """Show the number of events for this event type."""
        count = obj.events.count()
        if count > 0:
            url = reverse('admin:events_event_changelist') + f'?event_type__id__exact={obj.id}'
            return format_html('<a href="{}">{} events</a>', url, count)
        return "0 events"
    event_count.short_description = "Events"
    
    def get_queryset(self, request):
        """Optimize queryset with prefetch."""
        return super().get_queryset(request).prefetch_related('events')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """
    Admin interface for Event model with analytics capabilities.
    """
    list_display = [
        'title', 'event_type', 'user_link', 'target_link', 
        'timestamp', 'target_model', 'age_display'
    ]
    
    change_list_template = 'admin/events/event/change_list.html'
    list_filter = [
        'event_type__category', 'event_type', 'timestamp', 
        'content_type', 'user__is_staff'
    ]
    search_fields = [
        'title', 'description', 'user__username', 'user__email',
        'event_type__name', 'event_type__code'
    ]
    readonly_fields = [
        'event_type', 'user', 'target', 'title', 'description',
        'data_formatted', 'timestamp', 'ip_address', 'user_agent',
        'created_at', 'age_display', 'target_model'
    ]
    date_hierarchy = 'timestamp'
    
    fieldsets = (
        ('Event Information', {
            'fields': ('event_type', 'title', 'description')
        }),
        ('Related Objects', {
            'fields': ('user', 'target', 'target_model')
        }),
        ('Event Data', {
            'fields': ('data_formatted',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('timestamp', 'age_display', 'ip_address', 'user_agent', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    def has_add_permission(self, request):
        """Disable manual event creation - events should be created automatically."""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Disable event editing - events should be immutable."""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Only allow deletion for superusers."""
        return request.user.is_superuser
    
    def user_link(self, obj):
        """Create a link to the user's admin page."""
        if obj.user:
            url = reverse('admin:auth_user_change', args=[obj.user.id])
            return format_html('<a href="{}">{}</a>', url, obj.user)
        return "System"
    user_link.short_description = "User"
    
    def target_link(self, obj):
        """Create a link to the target object's admin page if possible."""
        if obj.target:
            try:
                content_type = obj.content_type
                app_label = content_type.app_label
                model_name = content_type.model
                url = reverse(f'admin:{app_label}_{model_name}_change', args=[obj.object_id])
                return format_html('<a href="{}">{}</a>', url, str(obj.target)[:50])
            except:
                return str(obj.target)[:50]
        return "-"
    target_link.short_description = "Target"
    
    def target_model(self, obj):
        """Show the model name of the target object."""
        if obj.content_type:
            return f"{obj.content_type.app_label}.{obj.content_type.model}"
        return "-"
    target_model.short_description = "Target Model"
    
    def age_display(self, obj):
        """Show how long ago the event occurred."""
        age_hours = obj.age_in_hours
        if age_hours < 1:
            minutes = int(age_hours * 60)
            return f"{minutes}m ago"
        elif age_hours < 24:
            return f"{int(age_hours)}h ago"
        else:
            days = int(age_hours / 24)
            return f"{days}d ago"
    age_display.short_description = "Age"
    
    def data_formatted(self, obj):
        """Format the JSON data for better display."""
        if obj.data:
            formatted = json.dumps(obj.data, indent=2)
            return format_html('<pre style="white-space: pre-wrap;">{}</pre>', formatted)
        return "No data"
    data_formatted.short_description = "Event Data"
    
    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            'event_type', 'user', 'content_type'
        )
    
    def get_urls(self):
        """Add custom URLs for analytics views."""
        urls = super().get_urls()
        custom_urls = [
            path('analytics/', self.admin_site.admin_view(self.analytics_view), name='events_analytics'),
            path('analytics/data/', self.admin_site.admin_view(self.analytics_data), name='events_analytics_data'),
            path('analytics/app/', self.admin_site.admin_view(self.app_analytics_view), name='events_app_analytics'),
            path('analytics/app/data/', self.admin_site.admin_view(self.app_analytics_data), name='events_app_analytics_data'),
            path('export_csv/', self.admin_site.admin_view(self.export_csv), name='events_export_csv'),
        ]
        return custom_urls + urls
    
    def analytics_view(self, request):
        """
        User Engagement Dashboard: excludes staff users and analytics-category events.
        """
        # Get date range from request or default to last 30 days
        # Normalize to calendar-day boundaries so initial load matches AJAX updates
        days = int(request.GET.get('days', 30))
        now = timezone.now()
        end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start_of_window = end_of_today - timedelta(days=days)
        # Expose these in context for UI/debugging
        start_date = start_of_window
        end_date = end_of_today
        
        # Base queryset with engagement filters (exclude staff and analytics-category events)
        base_qs = Event.objects.select_related('event_type', 'user', 'content_type')\
            .exclude(user__is_staff=True)\
            .exclude(event_type__category='analytics')

        # Basic event statistics
        total_events = base_qs.count()
        events_in_period = base_qs.filter(
            timestamp__gte=start_date
        ).count()
        
        # Events by type
        events_by_type = base_qs.values(
            'event_type__name', 'event_type__code'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        # Events by day (histogram data) - Optimized single query approach
        from .analytics_optimizer import AnalyticsQueryOptimizer
        
        # Exactly "days" calendar days including today
        num_days = days
        
        # Replace N+1 queries with single optimized aggregation
        daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
            start_of_window, num_days, filters={
                'exclude_staff': True,
                'exclude_categories': ['analytics']
            }
        )
        events_by_day = daily_aggregates['events_by_day']
        fast_joins_by_day = daily_aggregates['fast_joins_by_day']
        fast_leaves_by_day = daily_aggregates['fast_leaves_by_day']
        
        # Top users by activity
        top_users = base_qs.exclude(
            user__isnull=True
        ).values(
            'user__username', 'user__id'
        ).annotate(
            event_count=Count('id')
        ).order_by('-event_count')[:10]
        
        # Fast join/leave totals for the period (derived from per-day buckets to align with charts)
        fast_joins = sum(fast_joins_by_day.values())
        fast_leaves = sum(fast_leaves_by_day.values())
        
        # Recent milestones
        milestones = base_qs.filter(
            event_type__code=EventType.FAST_PARTICIPANT_MILESTONE,
            timestamp__gte=start_of_window
        ).order_by('-timestamp')[:5]
        
        # Get current and upcoming fasts
        from hub.models import Fast, Day
        today = timezone.now().date()
        
        # Current fasts (have days that include today)
        current_fasts = Fast.objects.filter(
            days__date=today
        ).distinct()
        
        # Upcoming fasts (have days that start after today)
        upcoming_fasts = Fast.objects.filter(
            days__date__gt=today
        ).distinct().order_by('days__date')[:3]  # Limit to next 3 upcoming fasts
        
        # Get join/leave data for current and upcoming fasts - Optimized
        # Combine current and upcoming fasts for analysis
        all_relevant_fasts = list(current_fasts) + list(upcoming_fasts)
        
        # Get fast-specific data with optimized queries
        current_upcoming_fast_data = AnalyticsQueryOptimizer.get_fast_specific_daily_data(
            all_relevant_fasts, start_of_window, num_days, filters={
                'exclude_staff': True,
                'exclude_categories': ['analytics']
            }
        )
        
        # Hourly distribution for the last 7 days (for more granular analysis)
        hourly_data = {}
        if days <= 7:
            for i in range(24):
                hour_start = end_of_today - timedelta(days=1)
                hour_start = hour_start.replace(hour=i, minute=0, second=0, microsecond=0)
                hour_end = hour_start + timedelta(hours=1)
                count = base_qs.filter(
                    timestamp__gte=hour_start,
                    timestamp__lt=hour_end
                ).count()
                hourly_data[f"{i:02d}:00"] = count
        
        # Fast activity trends (joins vs leaves over time)
        fast_trends_data = {
            'labels': list(events_by_day.keys()),
            'joins': list(fast_joins_by_day.values()),
            'leaves': list(fast_leaves_by_day.values()),
            'net': [joins - leaves for joins, leaves in zip(fast_joins_by_day.values(), fast_leaves_by_day.values())]
        }
        
        context = {
            'title': 'User Engagement Dashboard',
            'total_events': total_events,
            'events_in_period': sum(events_by_day.values()),
            'events_by_type': list(events_by_type),  # Convert to list for JSON serialization
            'events_by_day': events_by_day,
            'fast_joins_by_day': fast_joins_by_day,
            'fast_leaves_by_day': fast_leaves_by_day,
            'fast_trends_data': fast_trends_data,
            'top_users': list(top_users),  # Convert to list for JSON serialization
            'fast_joins': fast_joins,
            'fast_leaves': fast_leaves,
            'net_joins': fast_joins - fast_leaves,
            'milestones': milestones,
            'hourly_data': hourly_data,
            'current_upcoming_fast_data': current_upcoming_fast_data,
            'current_fasts': list(current_fasts),
            'upcoming_fasts': list(upcoming_fasts),
            'start_date': start_date,
            'end_date': end_date,
            'days': days,
        }
        
        return render(request, 'admin/events/analytics.html', context)
    
    def analytics_data(self, request):
        """
        AJAX endpoint for fetching analytics data with different date ranges.
        """
        from django.http import JsonResponse
        from django.contrib.contenttypes.models import ContentType
        
        try:
            # Get date range from request with validation
            days = int(request.GET.get('days', 30))
            
            # Validate date range
            if days <= 0 or days > 365:
                return JsonResponse({
                    'error': 'Invalid date range. Must be between 1 and 365 days.'
                }, status=400)
            
            # Use a rolling window of the last N days to avoid edge-of-midnight zeros
            now = timezone.now()
            start_of_window = now - timedelta(days=days)
            
        except (ValueError, TypeError) as e:
            return JsonResponse({
                'error': f'Invalid parameters: {str(e)}'
            }, status=400)
        
        # Events by day (histogram data) - Optimized single query approach
        from .analytics_optimizer import AnalyticsQueryOptimizer
        
        # Exactly "days" calendar days including today
        num_days = days
        
        # Replace N+1 queries with single optimized aggregation
        daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
            start_of_window, num_days, filters={
                'exclude_staff': True,
                'exclude_categories': ['analytics']
            }
        )
        events_by_day = daily_aggregates['events_by_day']
        fast_joins_by_day = daily_aggregates['fast_joins_by_day']
        fast_leaves_by_day = daily_aggregates['fast_leaves_by_day']
        
        # Fast activity trends
        fast_trends_data = {
            'labels': list(events_by_day.keys()),
            'joins': list(fast_joins_by_day.values()),
            'leaves': list(fast_leaves_by_day.values()),
            'net': [joins - leaves for joins, leaves in zip(fast_joins_by_day.values(), fast_leaves_by_day.values())]
        }
        
        # Get current and upcoming fasts
        from hub.models import Fast, Day
        today = timezone.now().date()
        
        # Current fasts (have days that include today)
        current_fasts = Fast.objects.filter(
            days__date=today
        ).distinct()
        
        # Upcoming fasts (have days that start after today)
        upcoming_fasts = Fast.objects.filter(
            days__date__gt=today
        ).distinct().order_by('days__date')[:3]  # Limit to next 3 upcoming fasts
        
        # Get join/leave data for current and upcoming fasts
        current_upcoming_fast_data = {}
        
        # Combine current and upcoming fasts for analysis - Optimized
        all_relevant_fasts = list(current_fasts) + list(upcoming_fasts)
        
        # Get fast-specific data with optimized queries
        current_upcoming_fast_data = AnalyticsQueryOptimizer.get_fast_specific_daily_data(
            all_relevant_fasts, start_of_window, num_days, filters={
                'exclude_staff': True,
                'exclude_categories': ['analytics']
            }
        )
        
        try:
            # Summary statistics aligned with per-day buckets
            fast_joins = sum(fast_joins_by_day.values())
            fast_leaves = sum(fast_leaves_by_day.values())
            
            return JsonResponse({
                'events_by_day': events_by_day,
                'fast_trends_data': fast_trends_data,
                'fast_joins': fast_joins,
                'fast_leaves': fast_leaves,
                'net_joins': fast_joins - fast_leaves,
                'events_in_period': sum(events_by_day.values()),
                'current_upcoming_fast_data': current_upcoming_fast_data,
            })
            
        except Exception as e:
            # Log the error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Analytics data generation failed: {str(e)}", exc_info=True)
            
            return JsonResponse({
                'error': 'An error occurred while generating analytics data. Please try again.'
            }, status=500)
    
    def export_csv(self, request):
        """
        Export events as CSV.
        """
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="events.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Timestamp', 'Event Type', 'User', 'Title', 'Description',
            'Target Type', 'Target ID', 'IP Address'
        ])
        
        # Get recent events (limit to avoid memory issues)
        events = Event.objects.select_related(
            'event_type', 'user', 'content_type'
        ).order_by('-timestamp')[:10000]
        
        for event in events:
            writer.writerow([
                event.timestamp.isoformat(),
                event.event_type.code,
                event.user.username if event.user else 'System',
                event.title,
                event.description,
                event.target_model_name or '',
                event.object_id or '',
                event.ip_address or '',
            ])
        
        return response

    def app_analytics_view(self, request):
        """
        App Analytics Dashboard: shows analytics-category events only.
        """
        from django.http import JsonResponse
        from django.db.models.functions import ExtractHour

        days = int(request.GET.get('days', 30))
        now = timezone.now()
        end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start_of_window = end_of_today - timedelta(days=days)

        # Filters for analytics-only and exclude staff
        analytics_filters = {
            'exclude_staff': True,
            'include_categories': ['analytics']
        }

        base_qs = Event.objects.select_related('event_type', 'user').filter(
            timestamp__gte=start_of_window,
            timestamp__lt=end_of_today,
            event_type__category='analytics'
        ).exclude(user__is_staff=True)

        screen_view_qs = base_qs.filter(
            event_type__code=EventType.SCREEN_VIEW,
            data__source='app_ui'
        )

        # Totals
        total_app_opens = base_qs.filter(event_type__code=EventType.APP_OPEN).count()
        total_screen_views = screen_view_qs.count()
        active_users = base_qs.exclude(user__isnull=True).values('user').distinct().count()

        # Events over time (analytics)
        from .analytics_optimizer import AnalyticsQueryOptimizer
        daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
            start_of_window, days, filters=analytics_filters
        )
        events_by_day = daily_aggregates['events_by_day']

        # App opens by hour of day (0-23) within window
        from django.db.models import IntegerField
        from django.db.models.functions import ExtractHour
        app_open_hours = base_qs.filter(event_type__code=EventType.APP_OPEN).annotate(
            hour=ExtractHour('timestamp')
        ).values('hour').annotate(count=Count('id')).order_by('hour')
        app_open_hourly = {f"{h:02d}": 0 for h in range(24)}
        for row in app_open_hours:
            hour = row['hour'] if row['hour'] is not None else 0
            app_open_hourly[f"{int(hour):02d}"] = row['count']

        # Most viewed screens
        top_screens = list(
            screen_view_qs.filter(data__has_key='screen')
            .values('data__screen').annotate(count=Count('id')).order_by('-count')[:15]
        )

        # Most active platforms (from app_open)
        platform_counts = list(
            base_qs.filter(event_type__code=EventType.APP_OPEN, data__has_key='platform')
            .values('data__platform').annotate(count=Count('id')).order_by('-count')
        )

        # Sessions per user (top)
        sessions_per_user_top = list(
            base_qs.filter(event_type__code=EventType.SESSION_START)
            .exclude(user__isnull=True)
            .values('user__username').annotate(session_count=Count('id'))
            .order_by('-session_count')[:10]
        )

        # Average session duration from session_end events
        avg_session_duration = base_qs.filter(event_type__code=EventType.SESSION_END, data__has_key='duration_seconds')\
            .aggregate(avg=Avg(Cast('data__duration_seconds', FloatField())))['avg'] or 0

        context = {
            'title': 'App Analytics Dashboard',
            'days': days,
            'events_by_day': events_by_day,
            'total_app_opens': total_app_opens,
            'total_screen_views': total_screen_views,
            'active_users': active_users,
            'avg_session_duration': int(avg_session_duration),
            'app_open_hourly': app_open_hourly,
            'top_screens': top_screens,
            'platform_counts': platform_counts,
            'sessions_per_user_top': sessions_per_user_top,
        }

        return render(request, 'admin/events/app_analytics.html', context)

    def app_analytics_data(self, request):
        """
        AJAX endpoint for App Analytics Dashboard.
        """
        from django.http import JsonResponse
        from django.db.models.functions import ExtractHour
        from django.db.models import Avg

        try:
            days = int(request.GET.get('days', 30))
            if days <= 0 or days > 365:
                return JsonResponse({'error': 'Invalid date range. Must be between 1 and 365 days.'}, status=400)
            now = timezone.now()
            end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            start_of_window = end_of_today - timedelta(days=days)
        except (ValueError, TypeError) as e:
            return JsonResponse({'error': f'Invalid parameters: {str(e)}'}, status=400)

        base_qs = Event.objects.select_related('event_type', 'user').filter(
            timestamp__gte=start_of_window,
            timestamp__lt=end_of_today,
            event_type__category='analytics'
        ).exclude(user__is_staff=True)

        # Events over time
        from .analytics_optimizer import AnalyticsQueryOptimizer
        daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
            start_of_window, days, filters={
                'exclude_staff': True,
                'include_categories': ['analytics']
            }
        )

        # App opens by hour of day
        app_open_hours = base_qs.filter(event_type__code=EventType.APP_OPEN).annotate(
            hour=ExtractHour('timestamp')
        ).values('hour').annotate(count=Count('id')).order_by('hour')
        app_open_hourly = {f"{h:02d}": 0 for h in range(24)}
        for row in app_open_hours:
            hour = row['hour'] if row['hour'] is not None else 0
            app_open_hourly[f"{int(hour):02d}"] = row['count']

        # Other metrics
        total_app_opens = base_qs.filter(event_type__code=EventType.APP_OPEN).count()
        screen_view_qs = base_qs.filter(
            event_type__code=EventType.SCREEN_VIEW,
            data__source='app_ui'
        )

        total_screen_views = screen_view_qs.count()
        active_users = base_qs.exclude(user__isnull=True).values('user').distinct().count()
        avg_session_duration = base_qs.filter(event_type__code=EventType.SESSION_END, data__has_key='duration_seconds')\
            .aggregate(avg=Avg(Cast('data__duration_seconds', FloatField())))['avg'] or 0

        top_screens = list(
            screen_view_qs.filter(data__has_key='screen')
            .values('data__screen').annotate(count=Count('id')).order_by('-count')[:15]
        )

        platform_counts = list(
            base_qs.filter(event_type__code=EventType.APP_OPEN, data__has_key='platform')
            .values('data__platform').annotate(count=Count('id')).order_by('-count')
        )

        sessions_per_user_top = list(
            base_qs.filter(event_type__code=EventType.SESSION_START)
            .exclude(user__isnull=True)
            .values('user__username').annotate(session_count=Count('id'))
            .order_by('-session_count')[:10]
        )

        return JsonResponse({
            'events_by_day': daily_aggregates['events_by_day'],
            'app_open_hourly': app_open_hourly,
            'total_app_opens': total_app_opens,
            'total_screen_views': total_screen_views,
            'active_users': active_users,
            'avg_session_duration': int(avg_session_duration),
            'top_screens': top_screens,
            'platform_counts': platform_counts,
            'sessions_per_user_top': sessions_per_user_top,
        })


@admin.register(UserActivityFeed)
class UserActivityFeedAdmin(admin.ModelAdmin):
    """
    Admin interface for UserActivityFeed model with monitoring capabilities.
    """
    list_display = [
        'user', 'activity_type', 'title', 'is_read', 'created_at', 'age_display'
    ]
    list_filter = [
        'activity_type', 'is_read', 'created_at', 'user__is_active'
    ]
    search_fields = [
        'user__username', 'user__email', 'title', 'description'
    ]
    readonly_fields = [
        'created_at', 'read_at', 'age_display', 'target_type_display', 'object_id'
    ]
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    
    fieldsets = (
        ('User & Activity', {
            'fields': ('user', 'activity_type', 'title', 'description')
        }),
        ('Status', {
            'fields': ('is_read', 'read_at', 'created_at')
        }),
        ('Related Objects', {
            'fields': ('event', 'target_type_display', 'object_id'),
            'classes': ('collapse',)
        }),
        ('Data', {
            'fields': ('data',),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['mark_as_read', 'mark_as_unread', 'delete_old_items']
    
    def age_display(self, obj):
        """Show how long ago the activity occurred."""
        age_hours = (timezone.now() - obj.created_at).total_seconds() / 3600
        if age_hours < 1:
            minutes = int(age_hours * 60)
            return f"{minutes}m ago"
        elif age_hours < 24:
            return f"{int(age_hours)}h ago"
        else:
            days = int(age_hours / 24)
            return f"{days}d ago"
    age_display.short_description = 'Age'
    
    def target_type_display(self, obj):
        """Display target object type."""
        if obj.content_type:
            return f"{obj.content_type.app_label}.{obj.content_type.model}"
        return "None"
    target_type_display.short_description = 'Target Type'
    
    def mark_as_read(self, request, queryset):
        """Mark selected items as read."""
        updated = queryset.update(is_read=True, read_at=timezone.now())
        self.message_user(
            request, 
            f'Successfully marked {updated} items as read.'
        )
    mark_as_read.short_description = "Mark selected items as read"
    
    def mark_as_unread(self, request, queryset):
        """Mark selected items as unread."""
        updated = queryset.update(is_read=False, read_at=None)
        self.message_user(
            request, 
            f'Successfully marked {updated} items as unread.'
        )
    mark_as_unread.short_description = "Mark selected items as unread"
    
    def delete_old_items(self, request, queryset):
        """Delete old items based on retention policy."""
        from .models import UserActivityFeed
        
        # Use the model's cleanup method
        deleted_count = UserActivityFeed.cleanup_old_items(dry_run=False)
        self.message_user(
            request, 
            f'Successfully deleted {deleted_count} old items based on retention policy.'
        )
    delete_old_items.short_description = "Delete old items (retention policy)"
    
    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            'user', 'event', 'event__event_type', 'content_type'
        )
    
    def changelist_view(self, request, extra_context=None):
        """Add summary statistics to the changelist view."""
        extra_context = extra_context or {}
        
        # Get summary stats
        total_items = UserActivityFeed.objects.count()
        unread_count = UserActivityFeed.objects.filter(is_read=False).count()
        read_count = total_items - unread_count
        
        # Get activity type breakdown
        from django.db.models import Count
        type_counts = UserActivityFeed.objects.values('activity_type').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        extra_context.update({
            'total_items': total_items,
            'unread_count': unread_count,
            'read_count': read_count,
            'type_counts': type_counts,
        })
        
        return super().changelist_view(request, extra_context)


# Add analytics link to the admin index
def analytics_link():
    """Helper to create analytics link for admin index."""
    return format_html(
        '<a href="{}">👥 User Engagement Dashboard</a>',
        reverse('admin:events_analytics')
    )


# Register a custom admin site section for events
class EventsAdminSite:
    """Custom admin configuration for events."""
    
    def __init__(self):
        # Add analytics link to the admin index
        admin.site.index_template = 'admin/events/index_with_analytics.html'


@admin.register(UserMilestone)
class UserMilestoneAdmin(admin.ModelAdmin):
    """
    Admin interface for UserMilestone model.
    """
    list_display = [
        'user', 'milestone_type', 'milestone_type_display', 
        'related_object_display', 'achieved_at'
    ]
    list_filter = [
        'milestone_type', 'achieved_at', 'content_type'
    ]
    search_fields = [
        'user__username', 'user__email', 'user__profile__name'
    ]
    readonly_fields = [
        'achieved_at', 'milestone_type_display', 'related_object_display'
    ]
    ordering = ['-achieved_at']
    date_hierarchy = 'achieved_at'
    
    fieldsets = (
        ('User & Milestone', {
            'fields': ('user', 'milestone_type', 'milestone_type_display')
        }),
        ('Related Object', {
            'fields': ('content_type', 'object_id', 'related_object_display'),
            'classes': ('collapse',)
        }),
        ('Timeline', {
            'fields': ('achieved_at',)
        }),
        ('Additional Data', {
            'fields': ('data',),
            'classes': ('collapse',)
        }),
    )
    
    def milestone_type_display(self, obj):
        """Display the human-readable milestone type."""
        return obj.get_milestone_type_display()
    milestone_type_display.short_description = 'Milestone Type'
    
    def related_object_display(self, obj):
        """Display the related object if it exists."""
        if obj.related_object:
            return f"{obj.content_type.model.title()}: {obj.related_object}"
        return "No related object"
    related_object_display.short_description = 'Related Object'
    
    def has_add_permission(self, request):
        """Prevent manual creation of milestones - they should be awarded automatically."""
        return False


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    """
    Admin interface for Announcement model.
    """
    list_display = [
        'title', 'status', 'target_all_users', 'publish_at', 
        'expires_at', 'total_recipients', 'created_by'
    ]
    list_filter = [
        'status', 'target_all_users', 'publish_at', 'expires_at', 
        'created_at', 'target_churches'
    ]
    search_fields = ['title', 'description']
    readonly_fields = ['total_recipients', 'created_at', 'updated_at']
    ordering = ['-publish_at']
    date_hierarchy = 'publish_at'
    
    fieldsets = (
        ('Content', {
            'fields': ('title', 'description', 'url')
        }),
        ('Publication', {
            'fields': ('status', 'publish_at', 'expires_at')
        }),
        ('Targeting', {
            'fields': ('target_all_users', 'target_churches'),
            'description': 'Choose who should receive this announcement'
        }),
        ('Tracking', {
            'fields': ('total_recipients', 'created_by'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    filter_horizontal = ['target_churches']
    
    def save_model(self, request, obj, form, change):
        """Set created_by when saving."""
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related('created_by')
    
    actions = ['publish_announcements', 'archive_announcements']
    
    def publish_announcements(self, request, queryset):
        """Publish selected announcements."""
        published_count = 0
        for announcement in queryset.filter(status='draft'):
            announcement.publish(user=request.user)
            published_count += 1
        
        self.message_user(
            request,
            f"Published {published_count} announcements and created activity feed items."
        )
    publish_announcements.short_description = "Publish selected announcements"
    
    def archive_announcements(self, request, queryset):
        """Archive selected announcements."""
        updated = queryset.update(status='archived')
        self.message_user(
            request,
            f"Archived {updated} announcements."
        )
    archive_announcements.short_description = "Archive selected announcements"
