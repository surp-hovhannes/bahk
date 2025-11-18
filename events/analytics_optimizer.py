"""
Analytics query optimization module.
Provides high-performance analytics data aggregation to replace N+1 query patterns.
"""

from django.db.models import Count, Case, When, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta
from collections import defaultdict
from .models import Event, EventType


class AnalyticsQueryOptimizer:
    """
    Optimized analytics queries that replace N+1 patterns with single aggregated queries.
    """
    
    @staticmethod
    def get_daily_event_aggregates(start_of_window, num_days, filters=None):
        """
        Get daily event counts with a single optimized query instead of N loops.
        Includes intelligent caching for improved performance.

        Args:
            start_of_window: datetime start of analysis window
            num_days: number of days to analyze
            filters: optional dict with keys:
                - include_categories: list of event categories to include
                - exclude_categories: list of event categories to exclude
                - exclude_staff: bool to exclude staff user events
                - only_event_types: list of event type codes to include (exclusive)
                - exclude_event_types: list of event type codes to exclude

        Returns:
            dict: {
                'events_by_day': {'2025-01-15': 42, ...},
                'fast_joins_by_day': {'2025-01-15': 6, ...},
                'fast_leaves_by_day': {'2025-01-15': 1, ...}
            }
        """
        from .analytics_cache import AnalyticsCacheService
        
        # Only use cache when no filters are applied to avoid cache key explosion
        if not filters:
            cached_data = AnalyticsCacheService.get_daily_aggregates(start_of_window, num_days)
            if cached_data:
                return cached_data
        
        end_of_window = start_of_window + timedelta(days=num_days)
        
        # Single query with conditional aggregation using Django's database-agnostic date truncation
        from django.db.models.functions import TruncDate
        
        # Base queryset with optional filters
        queryset = Event.objects.filter(
            timestamp__gte=start_of_window,
            timestamp__lt=end_of_window
        )

        if filters:
            include_categories = filters.get('include_categories')
            exclude_categories = filters.get('exclude_categories')
            exclude_staff = filters.get('exclude_staff')
            only_event_types = filters.get('only_event_types')
            exclude_event_types = filters.get('exclude_event_types')

            if include_categories:
                queryset = queryset.filter(event_type__category__in=include_categories)
            if exclude_categories:
                queryset = queryset.exclude(event_type__category__in=exclude_categories)
            if exclude_staff:
                queryset = queryset.exclude(user__is_staff=True)
            if only_event_types:
                queryset = queryset.filter(event_type__code__in=only_event_types)
            if exclude_event_types:
                queryset = queryset.exclude(event_type__code__in=exclude_event_types)

        daily_stats = queryset.annotate(
            date=TruncDate('timestamp')
        ).values('date').annotate(
            total_events=Count('id'),
            fast_joins=Count(
                Case(
                    When(event_type__code=EventType.USER_JOINED_FAST, then=1),
                    default=None
                )
            ),
            fast_leaves=Count(
                Case(
                    When(event_type__code=EventType.USER_LEFT_FAST, then=1),
                    default=None
                )
            )
        ).order_by('date')
        
        # Initialize all days with zero counts
        # Note: A rolling window of N days can span N+1 calendar days
        # (e.g., last 24 hours from Oct 2 15:00 to Oct 3 15:00 spans 2 calendar days)
        events_by_day = {}
        fast_joins_by_day = {}
        fast_leaves_by_day = {}
        
        for i in range(num_days + 1):
            day = start_of_window + timedelta(days=i)
            date_str = day.strftime('%Y-%m-%d')
            events_by_day[date_str] = 0
            fast_joins_by_day[date_str] = 0
            fast_leaves_by_day[date_str] = 0
        
        # Fill in actual counts
        for stat in daily_stats:
            # Handle both datetime objects (PostgreSQL) and strings (SQLite)
            if hasattr(stat['date'], 'strftime'):
                date_str = stat['date'].strftime('%Y-%m-%d')
            else:
                date_str = str(stat['date'])
            
            if date_str in events_by_day:  # Only include dates in our window
                events_by_day[date_str] = stat['total_events']
                fast_joins_by_day[date_str] = stat['fast_joins']
                fast_leaves_by_day[date_str] = stat['fast_leaves']
        
        result = {
            'events_by_day': events_by_day,
            'fast_joins_by_day': fast_joins_by_day,
            'fast_leaves_by_day': fast_leaves_by_day
        }
        
        # Cache the result when no filters are applied
        if not filters:
            AnalyticsCacheService.set_daily_aggregates(start_of_window, num_days, result)
        
        return result
    
    @staticmethod
    def get_fast_specific_daily_data(fast_queryset, start_of_window, num_days, filters=None):
        """
        Get daily join/leave data for specific fasts with optimized queries.

        Args:
            fast_queryset: QuerySet of Fast objects to analyze
            start_of_window: datetime start of analysis window
            num_days: number of days to analyze
            filters: optional dict with keys:
                - include_categories: list of event categories to include
                - exclude_categories: list of event categories to exclude
                - exclude_staff: bool to exclude staff user events
                - only_event_types: list of event type codes to include (exclusive)
                - exclude_event_types: list of event type codes to exclude

        Returns:
            dict: {fast_name: {'daily_joins': {...}, 'daily_leaves': {...}, ...}}
        """
        from django.contrib.contenttypes.models import ContentType
        from hub.models import Fast
        
        end_of_window = start_of_window + timedelta(days=num_days)
        fast_content_type = ContentType.objects.get_for_model(Fast)
        
        result = {}
        
        for fast in fast_queryset:
            # Get daily data for this fast with a single query using Django's database-agnostic date truncation
            from django.db.models.functions import TruncDate
            
            base_qs = Event.objects.filter(
                content_type=fast_content_type,
                object_id=fast.id,
                timestamp__gte=start_of_window,
                timestamp__lt=end_of_window
            )

            if filters:
                include_categories = filters.get('include_categories')
                exclude_categories = filters.get('exclude_categories')
                exclude_staff = filters.get('exclude_staff')
                only_event_types = filters.get('only_event_types')
                exclude_event_types = filters.get('exclude_event_types')

                if include_categories:
                    base_qs = base_qs.filter(event_type__category__in=include_categories)
                if exclude_categories:
                    base_qs = base_qs.exclude(event_type__category__in=exclude_categories)
                if exclude_staff:
                    base_qs = base_qs.exclude(user__is_staff=True)
                if only_event_types:
                    base_qs = base_qs.filter(event_type__code__in=only_event_types)
                if exclude_event_types:
                    base_qs = base_qs.exclude(event_type__code__in=exclude_event_types)

            daily_stats = base_qs.annotate(
                date=TruncDate('timestamp')
            ).values('date').annotate(
                joins=Count(
                    Case(
                        When(event_type__code=EventType.USER_JOINED_FAST, then=1),
                        default=None
                    )
                ),
                leaves=Count(
                    Case(
                        When(event_type__code=EventType.USER_LEFT_FAST, then=1),
                        default=None
                    )
                )
            ).order_by('date')
            
            # Initialize all days
            # Note: A rolling window of N days can span N+1 calendar days
            daily_joins = {}
            daily_leaves = {}
            for i in range(num_days + 1):
                day = start_of_window + timedelta(days=i)
                date_str = day.strftime('%Y-%m-%d')
                daily_joins[date_str] = 0
                daily_leaves[date_str] = 0
            
            # Fill actual data
            for stat in daily_stats:
                # Handle both datetime objects (PostgreSQL) and strings (SQLite)
                if hasattr(stat['date'], 'strftime'):
                    date_str = stat['date'].strftime('%Y-%m-%d')
                else:
                    date_str = str(stat['date'])
                    
                if date_str in daily_joins:
                    daily_joins[date_str] = stat['joins']
                    daily_leaves[date_str] = stat['leaves']
            
            # Get fast date range
            fast_days = fast.days.order_by('date')
            if fast_days.exists():
                fast_start = fast_days.first().date
                fast_end = fast_days.last().date
            else:
                continue
            
            # Check if current or upcoming
            today = timezone.now().date()
            is_current = fast.days.filter(date=today).exists()
            is_upcoming = fast.days.filter(date__gt=today).exists()
            
            # Get total counts for the period
            total_joins = sum(daily_joins.values())
            total_leaves = sum(daily_leaves.values())
            
            result[fast.name] = {
                'is_current': is_current,
                'is_upcoming': is_upcoming,
                'start_date': fast_start.isoformat(),
                'end_date': fast_end.isoformat(),
                'total_joins': total_joins,
                'total_leaves': total_leaves,
                'net_growth': total_joins - total_leaves,
                'daily_joins': daily_joins,
                'daily_leaves': daily_leaves,
                'participant_count': fast.profiles.count()
            }
        
        return result
