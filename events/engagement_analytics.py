"""Data snapshots shared by the engagement admin page and range endpoint."""

import logging
from datetime import timedelta, timezone as dt_timezone
from hashlib import sha256

from django.core.cache import DEFAULT_CACHE_ALIAS, caches
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from hub.models import Fast

from .analytics_optimizer import AnalyticsQueryOptimizer
from .models import EventType
from .participation_analytics import analytics_events

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 300
QUEUE_EXPIRY = 20
COMPUTE_LIMIT = 40
# Cover the longest queue wait plus execution, with room for publishing.
LEASE_TTL = 65


def engagement_window(days):
    now = timezone.now()
    end_date = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return end_date - timedelta(days=days), end_date


def snapshot_key(days, end_date):
    window = f'{end_date.isoformat()}:{end_date.tzinfo}'
    digest = sha256(window.encode()).hexdigest()[:24]
    return f'admin:engagement:v1:{days}:{digest}'


def engagement_snapshot_cache():
    alias = 'engagement_snapshot'
    return caches[alias if alias in caches else DEFAULT_CACHE_ALIAS]


def get_engagement_snapshot(days, end_date):
    """Return (snapshot, status), scheduling a refresh on a cache miss."""
    from .tasks import refresh_engagement_snapshot

    key = snapshot_key(days, end_date)
    try:
        cache = engagement_snapshot_cache()
        snapshot = cache.get(key)
        if snapshot is not None:
            return snapshot, 200
        # Let the lease expire even on publish failure. A failed publish can
        # still have reached the broker, and other requests must not fan out.
        if cache.add(f'{key}:pending', True, timeout=LEASE_TTL):
            task = refresh_engagement_snapshot
            with task.app.connection_for_write(
                connect_timeout=2,
                transport_options={
                    **task.app.conf.broker_transport_options,
                    'socket_connect_timeout': 2,
                    'socket_timeout': 2,
                },
            ) as connection:
                with task.app.amqp.Producer(connection) as producer:
                    task.app.send_task(
                        task.name, args=(days, key), producer=producer,
                        expires=QUEUE_EXPIRY, time_limit=COMPUTE_LIMIT,
                        retry=False, ignore_result=True,
                    )
    except Exception:
        logger.exception('Could not load or queue engagement analytics')
        return None, 503
    return None, 202


def _daily_counts(queryset, date_keys, window_tz):
    """
    Build a date-keyed dict covering the window for the queryset.

    Args:
        queryset: Event queryset to aggregate
        date_keys: Dict keys (dates as 'YYYY-MM-DD' strings) to initialize with zero
        window_tz: Timezone used to form the reporting window and date keys

    Returns:
        Dict mapping date strings to event counts
    """
    counts = {date: 0 for date in date_keys}
    daily_rows = queryset.annotate(
        date=TruncDate('timestamp', tzinfo=window_tz)
    ).values('date').annotate(count=Count('id')).order_by('date')

    for row in daily_rows:
        date_value = row['date']
        date_str = date_value.strftime('%Y-%m-%d') if hasattr(date_value, 'strftime') else str(date_value)
        if date_str in counts:
            counts[date_str] = row['count']

    return counts


def build_engagement_snapshot(days, start_date, end_date):
    """Compute the dashboard metrics in the reporting calendar window."""
    excluded_types = [
        EventType.APP_OPEN,
        EventType.SESSION_START,
        EventType.SESSION_END,
        EventType.SCREEN_VIEW,
    ]
    filters = {'exclude_staff': True, 'exclude_event_types': excluded_types}
    base_qs = analytics_events().exclude(user__is_staff=True).exclude(
        event_type__code__in=excluded_types
    )

    # Basic event statistics
    total_events = base_qs.count()

    # Events by type
    events_by_type_qs = base_qs.values(
        'event_type__name', 'event_type__code'
    ).annotate(
        count=Count('id')
    ).order_by('-count')

    # Take top-N, but ensure prayer-related event types are always included in the distribution.
    events_by_type = list(events_by_type_qs[:10])
    required_event_type_codes = [
        EventType.PRAYER_REQUEST_CREATED,
        EventType.PRAYER_REQUEST_ACCEPTED,
        EventType.PRAYER_REQUEST_COMPLETED,
        EventType.PRAYER_REQUEST_THANKS_SENT,
        EventType.PRAYER_SET_VIEWED,
        # Newer types (may not exist in older DBs)
        'prayer_viewed',
        'prayer_request_viewed',
    ]
    existing_codes = {row['event_type__code'] for row in events_by_type}
    missing_codes = [c for c in required_event_type_codes if c not in existing_codes]

    if missing_codes:
        extra_rows = list(
            events_by_type_qs.filter(event_type__code__in=missing_codes)
        )
        by_code = {row['event_type__code']: row for row in (events_by_type + extra_rows)}

        # Add explicit zero-count rows for codes with no events (so they still show in the chart).
        type_name_by_code = dict(
            EventType.objects.filter(code__in=missing_codes).values_list('code', 'name')
        )
        for code in missing_codes:
            if code not in by_code:
                by_code[code] = {
                    'event_type__code': code,
                    'event_type__name': type_name_by_code.get(code, code),
                    'count': 0,
                }

        events_by_type = sorted(by_code.values(), key=lambda r: r['count'], reverse=True)

    # Events by day (histogram data) - Optimized single query approach
    # Replace N+1 queries with single optimized aggregation
    # Exclude pure analytics events but keep engagement analytics
    daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
        start_date, days, filters=filters
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

    # Optimize KPI totals with single aggregated query
    kpi_totals = base_qs.filter(
        timestamp__gte=start_date,
        timestamp__lt=end_date,
    ).aggregate(
        user_signups=Count('id', filter=Q(event_type__code=EventType.USER_ACCOUNT_CREATED)),
        devotional_views=Count('id', filter=Q(event_type__code=EventType.DEVOTIONAL_VIEWED)),
        checklist_usage=Count('id', filter=Q(event_type__code=EventType.CHECKLIST_USED)),
        prayer_set_views=Count('id', filter=Q(event_type__code=EventType.PRAYER_SET_VIEWED)),
        prayer_request_activity=Count(
            'id',
            filter=Q(
                event_type__code__in=[
                    EventType.PRAYER_REQUEST_CREATED,
                    EventType.PRAYER_REQUEST_ACCEPTED,
                    EventType.PRAYER_REQUEST_COMPLETED,
                    EventType.PRAYER_REQUEST_THANKS_SENT,
                ]
            ),
        ),
    )
    user_signups_total = kpi_totals['user_signups']
    devotional_views_total = kpi_totals['devotional_views']
    checklist_usage_total = kpi_totals['checklist_usage']
    prayer_set_views_total = kpi_totals['prayer_set_views']
    prayer_request_activity_total = kpi_totals['prayer_request_activity']

    # Get daily breakdowns for each KPI
    window_tz = start_date.tzinfo or dt_timezone.utc
    signups_qs = base_qs.filter(
        event_type__code=EventType.USER_ACCOUNT_CREATED,
        timestamp__gte=start_date,
        timestamp__lt=end_date,
    )
    user_signups_by_day = _daily_counts(signups_qs, events_by_day.keys(), window_tz)

    devotional_qs = base_qs.filter(
        event_type__code=EventType.DEVOTIONAL_VIEWED,
        timestamp__gte=start_date,
        timestamp__lt=end_date,
    )
    devotional_views_by_day = _daily_counts(devotional_qs, events_by_day.keys(), window_tz)

    checklist_qs = base_qs.filter(
        event_type__code=EventType.CHECKLIST_USED,
        timestamp__gte=start_date,
        timestamp__lt=end_date,
    )
    checklist_usage_by_day = _daily_counts(checklist_qs, events_by_day.keys(), window_tz)

    prayer_set_qs = base_qs.filter(
        event_type__code=EventType.PRAYER_SET_VIEWED,
        timestamp__gte=start_date,
        timestamp__lt=end_date,
    )
    prayer_set_views_by_day = _daily_counts(prayer_set_qs, events_by_day.keys(), window_tz)

    prayer_requests_qs = base_qs.filter(
        event_type__code__in=[
            EventType.PRAYER_REQUEST_CREATED,
            EventType.PRAYER_REQUEST_ACCEPTED,
            EventType.PRAYER_REQUEST_COMPLETED,
            EventType.PRAYER_REQUEST_THANKS_SENT,
        ],
        timestamp__gte=start_date,
        timestamp__lt=end_date,
    )
    prayer_request_activity_by_day = _daily_counts(prayer_requests_qs, events_by_day.keys(), window_tz)

    feature_usage_over_time = {
        'labels': list(events_by_day.keys()),
        'datasets': [
            {'label': 'User Signups', 'data': list(user_signups_by_day.values())},
            {'label': 'Devotional Views', 'data': list(devotional_views_by_day.values())},
            {'label': 'Checklist Uses', 'data': list(checklist_usage_by_day.values())},
            {'label': 'Prayer Set Views', 'data': list(prayer_set_views_by_day.values())},
            {'label': 'Prayer Requests', 'data': list(prayer_request_activity_by_day.values())},
        ],
    }

    # Language usage: count unique users by lang from recent events
    lang_events = base_qs.filter(
        timestamp__gte=start_date,
        timestamp__lt=end_date,
        data__has_key='lang',
    ).exclude(user__isnull=True).values_list('data', 'user_id')
    lang_user_sets = {}
    for event_data, user_id in lang_events.iterator():
        lang_val = event_data.get('lang') if event_data else None
        if lang_val in ('en', 'hy'):
            lang_user_sets.setdefault(lang_val, set()).add(user_id)
    language_en = len(lang_user_sets.get('en', set()))
    language_hy = len(lang_user_sets.get('hy', set()))

    # Cache only the fields the template displays.
    milestones = list(base_qs.filter(
        event_type__code=EventType.FAST_PARTICIPANT_MILESTONE,
        timestamp__gte=start_date
    ).order_by('-timestamp').values('title', 'timestamp')[:5])

    # Get current and upcoming fasts
    today = (end_date - timedelta(days=1)).date()

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
    # Exclude pure analytics events but keep engagement analytics
    current_upcoming_fast_data = AnalyticsQueryOptimizer.get_fast_specific_daily_data(
        all_relevant_fasts, start_date, days, filters=filters
    )

    # Hourly distribution for today on short windows.
    hourly_data = {}
    if days <= 7:
        for i in range(24):
            hour_start = end_date - timedelta(days=1)
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

    return {
        'total_events': total_events,
        'events_in_period': sum(events_by_day.values()),
        'events_by_type': events_by_type,
        'events_by_day': events_by_day,
        'fast_trends_data': fast_trends_data,
        'top_users': list(top_users),
        'fast_joins': fast_joins,
        'fast_leaves': fast_leaves,
        'net_joins': fast_joins - fast_leaves,
        'milestones': milestones,
        'hourly_data': hourly_data,
        'current_upcoming_fast_data': current_upcoming_fast_data,
        'user_signups': user_signups_total,
        'devotional_views': devotional_views_total,
        'checklist_usage': checklist_usage_total,
        'prayer_set_views': prayer_set_views_total,
        'prayer_request_activity': prayer_request_activity_total,
        'user_signups_by_day': user_signups_by_day,
        'devotional_views_by_day': devotional_views_by_day,
        'checklist_usage_by_day': checklist_usage_by_day,
        'prayer_set_views_by_day': prayer_set_views_by_day,
        'feature_usage_over_time': feature_usage_over_time,
        'language_en': language_en,
        'language_hy': language_hy,
    }
