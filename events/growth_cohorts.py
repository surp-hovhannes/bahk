"""Read-only weekly growth cohorts. Only aggregate cells leave this module."""

import math
import os
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Case, Count, DateField, Exists, F, Max, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Cast, TruncDate, TruncWeek

from events.event_schema import MEANINGFUL_EVENT_CODES
from events.models import Event, EventType
from events.participation_analytics import analytics_events, legacy_transition_events
from hub.models import FastParticipation

FALLBACK = 'America/Los_Angeles'
ACTIVATION = {'activation_24h': 1, 'activation_72h': 3, 'activation_7d': 7}
RETENTION = {'d1': (1, 1), 'd7': (7, 7), 'd30': (30, 30),
             'return_days_1_7': (1, 7), 'return_days_8_30': (8, 30)}
CAVEATS = [
    'Cross-sectional observational cohorts; small n does not establish causality.',
    'Retention is exact-day, not cumulative; any-return windows are separately labelled.',
    'screen_view is not trended across the client_ui cutoff; exposure/session events never activate.',
    'Activation uses elapsed time and a 24h ingestion lag; retention uses fully closed local days.',
]


def _configured(name):
    value = getattr(settings, name, os.environ.get(name, ''))
    return [str(v).strip() for v in (value.split(',') if isinstance(value, str) else value or []) if str(v).strip()]


def _cell(count, minimum):
    return count if count >= minimum else f'<{minimum}'


def _metric(n, d, immature, minimum):
    result = {'numerator': _cell(n, minimum), 'mature_denominator': _cell(d, minimum),
              'immature': _cell(immature, minimum), 'percent': None, 'wilson_95': None}
    # Also protect the complementary cell against subtraction disclosure.
    if d and min(n, d, d - n) >= minimum:
        p, z = n / d, 1.959963984540054
        center = (p + z * z / (2 * d)) / (1 + z * z / d)
        radius = z * math.sqrt(p * (1 - p) / d + z * z / (4 * d * d)) / (1 + z * z / d)
        result.update(percent=round(100 * p, 2),
                      wilson_95=[round(100 * (center - radius), 2), round(100 * (center + radius), 2)])
    return result


def build_report(as_of, min_cell=10):
    """as_of is an exclusive UTC midnight; signup weeks are Monday-based UTC.

    A fixed set of grouped SQL queries runs per distinct stored timezone, never
    per user/week. Correlated subqueries use the database to inspect actions.
    """
    if min_cell < 1:
        raise ValueError('min_cell must be positive')
    end = datetime.combine(as_of, time.min, tzinfo=timezone.utc)
    cutoff = end - timedelta(days=1)
    users = get_user_model().objects.filter(date_joined__lt=end)
    excluded = {}
    rules = [('staff', Q(is_staff=True)), ('superuser', Q(is_superuser=True))]
    ids = _configured('ANALYTICS_EXCLUDED_USER_IDS')
    if any(not v.isdecimal() for v in ids):
        raise ValueError('ANALYTICS_EXCLUDED_USER_IDS must contain integer IDs')
    rules.append(('configured_ids', Q(pk__in=[int(v) for v in ids])))
    domains = Q(pk__in=[])
    for domain in _configured('ANALYTICS_EXCLUDED_EMAIL_DOMAINS'):
        domains |= Q(email__iendswith='@' + domain.lstrip('@'))
    rules.append(('configured_email_domains', domains))
    for name, rule in rules:
        excluded[name] = _cell(users.filter(rule).count(), min_cell)
        users = users.exclude(rule)

    events = analytics_events().filter(event_type__code__in=MEANINGFUL_EVENT_CODES).exclude(
        event_type__code=EventType.USER_JOINED_FAST)
    legacy = legacy_transition_events(EventType.USER_JOINED_FAST)
    events = analytics_events().filter(Q(pk__in=events.values('pk')) | Q(pk__in=legacy.values('pk')))
    events = events.filter(user_id=OuterRef('pk'), timestamp__gte=OuterRef('date_joined'), timestamp__lt=end)
    periods = FastParticipation.objects.filter(profile__user_id=OuterRef('pk'),
                                               joined_at__gte=OuterRef('date_joined'), joined_at__lt=end)
    grouped = defaultdict(lambda: defaultdict(int))
    for stored_tz in users.order_by().values_list('profile__timezone', flat=True).distinct():
        fallback = stored_tz in (None, '', 'UTC')
        try:
            tz = ZoneInfo(FALLBACK if fallback else stored_tz)
        except (ZoneInfoNotFoundError, ValueError):
            tz, fallback = ZoneInfo(FALLBACK), True
        bucket = users.filter(profile__timezone=stored_tz) if stored_tz is not None else users.filter(profile__timezone__isnull=True)
        bucket = bucket.annotate(
            signup_day=TruncDate('date_joined', tzinfo=tz),
            week=TruncWeek('date_joined', tzinfo=timezone.utc),
            event_first=Subquery(events.order_by('timestamp', 'event_type__code', 'pk').values('timestamp')[:1]),
            period_first=Subquery(periods.order_by('joined_at').values('joined_at')[:1]),
            event_code=Subquery(events.order_by('timestamp', 'event_type__code', 'pk').values('event_type__code')[:1]),
        ).annotate(
            first=Case(When(Q(period_first__isnull=False) & (Q(event_first__isnull=True) |
                       Q(period_first__lte=F('event_first'))), then=F('period_first')), default=F('event_first')),
            first_code=Case(When(Q(period_first__isnull=False) & (Q(event_first__isnull=True) |
                            Q(period_first__lte=F('event_first'))), then=Value(EventType.USER_JOINED_FAST)),
                            default=F('event_code')),
        )
        aggregates = {'population': Count('pk')}
        for name, days in ACTIVATION.items():
            mature = Q(date_joined__lte=cutoff - timedelta(days=days))
            hit = Q(first__lte=F('date_joined') + timedelta(days=days))
            aggregates[name + '_d'] = Count('pk', filter=mature)
            aggregates[name + '_n'] = Count('pk', filter=mature & hit)
        for name, (start, stop) in RETENTION.items():
            # Date arithmetic stays in predicates: SQL datetime arithmetic on
            # date-valued annotations is backend-dependent.
            closed = end.astimezone(tz).date() - timedelta(days=stop + 1)
            mature = Q(signup_day__lte=closed)
            day_events = events.annotate(day=TruncDate('timestamp', tzinfo=tz))
            day_periods = periods.annotate(day=TruncDate('joined_at', tzinfo=tz))
            # Compare signup day to action day shifted by calendar-day offsets.
            day_events = day_events.filter(day__gte=Cast(OuterRef('signup_day') + timedelta(days=start), DateField()),
                                           day__lte=Cast(OuterRef('signup_day') + timedelta(days=stop), DateField()))
            day_periods = day_periods.filter(day__gte=Cast(OuterRef('signup_day') + timedelta(days=start), DateField()),
                                             day__lte=Cast(OuterRef('signup_day') + timedelta(days=stop), DateField()))
            bucket = bucket.annotate(**{name + '_hit': Exists(day_events) | Exists(day_periods)})
            aggregates[name + '_d'] = Count('pk', filter=mature)
            aggregates[name + '_n'] = Count('pk', filter=mature & Q(**{name + '_hit': True}))
        for code in sorted(MEANINGFUL_EVENT_CODES):
            aggregates['first_' + code] = Count('pk', filter=Q(
                date_joined__lte=cutoff - timedelta(days=7), first__lte=F('date_joined') + timedelta(days=7),
                first_code=code))
        for row in bucket.values('week').annotate(**aggregates).order_by():
            target = grouped[row.pop('week').date().isoformat()]
            for key, count in row.items():
                target[key] += count
            if fallback:
                target['fallback'] += row['population']

    cohorts = []
    for week, counts in sorted(grouped.items()):
        population = counts['population']
        metrics = {name: _metric(counts[name + '_n'], counts[name + '_d'],
                                population - counts[name + '_d'], min_cell)
                   for name in (*ACTIVATION, *RETENTION)}
        cohorts.append({'week_start': week, 'population': _cell(population, min_cell),
                        'timezone_fallback': _metric(counts['fallback'], population, 0, min_cell),
                        'metrics': metrics, 'first_action_within_7d': {
                            code: _metric(counts['first_' + code], counts['activation_7d_n'],
                                          population - counts['activation_7d_d'], min_cell)
                            for code in sorted(MEANINGFUL_EVENT_CODES)}})
    freshness = Event.objects.filter(timestamp__lt=end).aggregate(latest=Max('timestamp'))['latest']
    return {'as_of': end.isoformat(), 'activation_data_through': cutoff.isoformat(),
            'data_freshness': freshness.isoformat() if freshness else None,
            'min_cell': min_cell, 'cohort_timezone': 'UTC', 'exclusions_sequential': excluded,
            'caveats': CAVEATS, 'cohorts': cohorts}
