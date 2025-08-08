"""
Admin interface for the events app.
Provides comprehensive views for events, event types, and analytics.
"""

from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils import timezone
from datetime import timedelta
import json
import csv

from .models import Event, EventType, UserActivityFeed


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
            path('export_csv/', self.admin_site.admin_view(self.export_csv), name='events_export_csv'),
        ]
        return custom_urls + urls
    
    def analytics_view(self, request):
        """
        Custom analytics view showing event statistics and trends.
        """
        # Get date range from request or default to last 30 days
        days = int(request.GET.get('days', 30))
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        # Basic event statistics
        total_events = Event.objects.count()
        events_in_period = Event.objects.filter(
            timestamp__gte=start_date
        ).count()
        
        # Events by type
        events_by_type = Event.objects.values(
            'event_type__name', 'event_type__code'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        # Events by day (histogram data)
        events_by_day = {}
        fast_joins_by_day = {}
        fast_leaves_by_day = {}
        
        # TODO: As dataset grows, we need to address pagination and caching
        # for better performance when processing large date ranges
        for i in range(days):
            day = start_date + timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            
            # Total events for this day
            count = Event.objects.filter(
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            events_by_day[day.strftime('%Y-%m-%d')] = count
            
            # Fast joins for this day
            joins_count = Event.objects.filter(
                event_type__code=EventType.USER_JOINED_FAST,
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            fast_joins_by_day[day.strftime('%Y-%m-%d')] = joins_count
            
            # Fast leaves for this day
            leaves_count = Event.objects.filter(
                event_type__code=EventType.USER_LEFT_FAST,
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            fast_leaves_by_day[day.strftime('%Y-%m-%d')] = leaves_count
        
        # Top users by activity
        top_users = Event.objects.exclude(
            user__isnull=True
        ).values(
            'user__username', 'user__id'
        ).annotate(
            event_count=Count('id')
        ).order_by('-event_count')[:10]
        
        # Fast join/leave totals for the period
        fast_joins = Event.objects.filter(
            event_type__code=EventType.USER_JOINED_FAST,
            timestamp__gte=start_date
        ).count()
        
        fast_leaves = Event.objects.filter(
            event_type__code=EventType.USER_LEFT_FAST,
            timestamp__gte=start_date
        ).count()
        
        # Recent milestones
        milestones = Event.objects.filter(
            event_type__code=EventType.FAST_PARTICIPANT_MILESTONE,
            timestamp__gte=start_date
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
        
        # Get join/leave data for current and upcoming fasts
        current_upcoming_fast_data = {}
        
        # Combine current and upcoming fasts for analysis
        all_relevant_fasts = list(current_fasts) + list(upcoming_fasts)
        
        for fast in all_relevant_fasts:
            # Get fast date range
            fast_days = fast.days.order_by('date')
            if not fast_days.exists():
                continue
                
            fast_start = fast_days.first().date
            fast_end = fast_days.last().date
            
            # Get join/leave events for this specific fast
            fast_joins = Event.objects.filter(
                event_type__code=EventType.USER_JOINED_FAST,
                content_type=ContentType.objects.get_for_model(fast),
                object_id=fast.id,
                timestamp__gte=start_date
            ).count()
            
            fast_leaves = Event.objects.filter(
                event_type__code=EventType.USER_LEFT_FAST,
                content_type=ContentType.objects.get_for_model(fast),
                object_id=fast.id,
                timestamp__gte=start_date
            ).count()
            
            # Get daily join/leave data for this fast
            fast_daily_joins = {}
            fast_daily_leaves = {}
            
            for i in range(days):
                day = start_date + timedelta(days=i)
                day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)
                
                # Joins for this fast on this day
                joins_count = Event.objects.filter(
                    event_type__code=EventType.USER_JOINED_FAST,
                    content_type=ContentType.objects.get_for_model(fast),
                    object_id=fast.id,
                    timestamp__gte=day_start,
                    timestamp__lt=day_end
                ).count()
                fast_daily_joins[day.strftime('%Y-%m-%d')] = joins_count
                
                # Leaves for this fast on this day
                leaves_count = Event.objects.filter(
                    event_type__code=EventType.USER_LEFT_FAST,
                    content_type=ContentType.objects.get_for_model(fast),
                    object_id=fast.id,
                    timestamp__gte=day_start,
                    timestamp__lt=day_end
                ).count()
                fast_daily_leaves[day.strftime('%Y-%m-%d')] = leaves_count
            
            current_upcoming_fast_data[fast.name] = {
                'is_current': fast in current_fasts,
                'is_upcoming': fast in upcoming_fasts,
                'start_date': fast_start.isoformat(),
                'end_date': fast_end.isoformat(),
                'total_joins': fast_joins,
                'total_leaves': fast_leaves,
                'net_growth': fast_joins - fast_leaves,
                'daily_joins': fast_daily_joins,
                'daily_leaves': fast_daily_leaves,
                'participant_count': fast.profiles.count()
            }
        
        # Hourly distribution for the last 7 days (for more granular analysis)
        hourly_data = {}
        if days <= 7:
            for i in range(24):
                hour_start = start_date.replace(hour=i, minute=0, second=0, microsecond=0)
                hour_end = hour_start + timedelta(hours=1)
                count = Event.objects.filter(
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
            'title': 'Events Analytics',
            'total_events': total_events,
            'events_in_period': events_in_period,
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
        
        # Get date range from request
        days = int(request.GET.get('days', 30))
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        # Events by day (histogram data)
        events_by_day = {}
        fast_joins_by_day = {}
        fast_leaves_by_day = {}
        
        for i in range(days):
            day = start_date + timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            
            # Total events for this day
            count = Event.objects.filter(
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            events_by_day[day.strftime('%Y-%m-%d')] = count
            
            # Fast joins for this day
            joins_count = Event.objects.filter(
                event_type__code=EventType.USER_JOINED_FAST,
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            fast_joins_by_day[day.strftime('%Y-%m-%d')] = joins_count
            
            # Fast leaves for this day
            leaves_count = Event.objects.filter(
                event_type__code=EventType.USER_LEFT_FAST,
                timestamp__gte=day_start,
                timestamp__lt=day_end
            ).count()
            fast_leaves_by_day[day.strftime('%Y-%m-%d')] = leaves_count
        
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
        
        # Combine current and upcoming fasts for analysis
        all_relevant_fasts = list(current_fasts) + list(upcoming_fasts)
        
        for fast in all_relevant_fasts:
            # Get fast date range
            fast_days = fast.days.order_by('date')
            if not fast_days.exists():
                continue
                
            fast_start = fast_days.first().date
            fast_end = fast_days.last().date
            
            # Get join/leave events for this specific fast
            fast_joins = Event.objects.filter(
                event_type__code=EventType.USER_JOINED_FAST,
                content_type=ContentType.objects.get_for_model(fast),
                object_id=fast.id,
                timestamp__gte=start_date
            ).count()
            
            fast_leaves = Event.objects.filter(
                event_type__code=EventType.USER_LEFT_FAST,
                content_type=ContentType.objects.get_for_model(fast),
                object_id=fast.id,
                timestamp__gte=start_date
            ).count()
            
            # Get daily join/leave data for this fast
            fast_daily_joins = {}
            fast_daily_leaves = {}
            
            for i in range(days):
                day = start_date + timedelta(days=i)
                day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)
                
                # Joins for this fast on this day
                joins_count = Event.objects.filter(
                    event_type__code=EventType.USER_JOINED_FAST,
                    content_type=ContentType.objects.get_for_model(fast),
                    object_id=fast.id,
                    timestamp__gte=day_start,
                    timestamp__lt=day_end
                ).count()
                fast_daily_joins[day.strftime('%Y-%m-%d')] = joins_count
                
                # Leaves for this fast on this day
                leaves_count = Event.objects.filter(
                    event_type__code=EventType.USER_LEFT_FAST,
                    content_type=ContentType.objects.get_for_model(fast),
                    object_id=fast.id,
                    timestamp__gte=day_start,
                    timestamp__lt=day_end
                ).count()
                fast_daily_leaves[day.strftime('%Y-%m-%d')] = leaves_count
            
            current_upcoming_fast_data[fast.name] = {
                'is_current': fast in current_fasts,
                'is_upcoming': fast in upcoming_fasts,
                'start_date': fast_start.isoformat(),
                'end_date': fast_end.isoformat(),
                'total_joins': fast_joins,
                'total_leaves': fast_leaves,
                'net_growth': fast_joins - fast_leaves,
                'daily_joins': fast_daily_joins,
                'daily_leaves': fast_daily_leaves,
                'participant_count': fast.profiles.count()
            }
        
        # Summary statistics
        fast_joins = Event.objects.filter(
            event_type__code=EventType.USER_JOINED_FAST,
            timestamp__gte=start_date
        ).count()
        
        fast_leaves = Event.objects.filter(
            event_type__code=EventType.USER_LEFT_FAST,
            timestamp__gte=start_date
        ).count()
        
        return JsonResponse({
            'events_by_day': events_by_day,
            'fast_trends_data': fast_trends_data,
            'fast_joins': fast_joins,
            'fast_leaves': fast_leaves,
            'net_joins': fast_joins - fast_leaves,
            'events_in_period': sum(events_by_day.values()),
            'current_upcoming_fast_data': current_upcoming_fast_data,
        })
    
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
        'created_at', 'read_at', 'age_display', 'target_type_display'
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
            'fields': ('event', 'target_type_display', 'target_id'),
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
        '<a href="{}">📊 View Events Analytics</a>',
        reverse('admin:events_analytics')
    )


# Register a custom admin site section for events
class EventsAdminSite:
    """Custom admin configuration for events."""
    
    def __init__(self):
        # Add analytics link to the admin index
        admin.site.index_template = 'admin/events/index_with_analytics.html'
