"""Read-only weekly growth cohorts. Only aggregate cells leave this module."""

import math
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

from decouple import Csv, config
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import F, Max, Q

from events.event_schema import MEANINGFUL_EVENT_CODES
from events.models import EventType
from events.participation_analytics import analytics_events, legacy_transition_events
from hub.models import FastParticipation

ITERATOR_CHUNK_SIZE = 5000
FALLBACK = 'America/Los_Angeles'
ACTIVATION = {'activation_24h': 1, 'activation_72h': 3, 'activation_7d': 7}
RETENTION = {'d1': (1, 1), 'd7': (7, 7), 'd30': (30, 30),
             'return_days_1_7': (1, 7), 'return_days_8_30': (8, 30)}
# Every (smaller, larger) window pair whose band difference could be subtracted.
NESTED = ([(a, b) for a in ACTIVATION for b in ACTIVATION if ACTIVATION[a] < ACTIVATION[b]]
          + [(a, b) for a, (start, stop) in RETENTION.items() for b, (lo, hi) in RETENTION.items()
             if a != b and lo <= start and stop <= hi])
CAVEATS = [
    'Cross-sectional observational cohorts; small n does not establish causality.',
    'Retention is exact-day, not cumulative; any-return windows are separately labelled.',
    'screen_view is not trended across the client_ui cutoff; exposure/session events never activate.',
    'Activation uses elapsed time and a 24h ingestion lag; retention uses fully closed local days.',
]


def _configured(name):
    """Settings win; otherwise python-decouple reads the environment, then .env."""
    value = getattr(settings, name, None)
    if value is None:
        value = config(name, default='')
    if isinstance(value, str):
        value = Csv()(value)
    return [str(v).strip() for v in value if str(v).strip()]


def _cell(count, minimum):
    return count if count >= minimum else f'<{minimum}'


def _metric(n, d, immature, minimum):
    """Use immature=None for population diagnostics without a maturity window."""
    result = {'numerator': _cell(n, minimum), 'mature_denominator': _cell(d, minimum),
              'percent': None, 'wilson_95': None}
    if immature is not None:
        result['immature'] = _cell(immature, minimum)
    # Population is released separately. Hide both counts when subtracting
    # the denominator would recover the suppressed immature cell; hiding only
    # the denominator can still leak it when the numerator equals it.
    if immature is not None and immature < minimum:
        for key in ('numerator', 'mature_denominator'):
            if isinstance(result[key], int):
                result[key] = 'suppressed'
    elif immature is not None and d < minimum:
        # The converse: population minus immature would recover the denominator.
        result['immature'] = 'suppressed'
    elif d - n < minimum and isinstance(result['numerator'], int):
        result['numerator'] = 'suppressed'
    if d and min(n, d, d - n) >= minimum and (immature is None or immature >= minimum):
        p, z = n / d, 1.959963984540054
        center = (p + z * z / (2 * d)) / (1 + z * z / d)
        radius = z * math.sqrt(p * (1 - p) / d + z * z / (4 * d * d)) / (1 + z * z / d)
        result.update(percent=round(100 * p, 2),
                      wilson_95=[round(100 * (center - radius), 2), round(100 * (center + radius), 2)])
    return result


def _suppress_numerator(metric):
    metric.update(numerator='suppressed', percent=None, wilson_95=None)


def _suppress_nested(metrics, minimum):
    # Snapshot visibility so suppression order cannot change which pairs qualify.
    hidden = set()
    for smaller, larger in NESTED:
        a, b = metrics[smaller], metrics[larger]
        if (isinstance(a['numerator'], int) and isinstance(b['numerator'], int)
                and isinstance(a['mature_denominator'], int)
                and a['mature_denominator'] == b['mature_denominator']
                and 0 < b['numerator'] - a['numerator'] < minimum):
            hidden.add(larger)
    for name in hidden:
        _suppress_numerator(metrics[name])


def _suppress_distribution(first_actions, counts, minimum):
    """A released total minus the visible categories gives the hidden sum.

    Every 'suppressed' cell is known to be at least the threshold, so only the
    slack above that is uncertain. Hide the smallest visible categories (ties by
    code) until the slack is a full threshold: otherwise '<k' cells and the
    hidden categories are pinned to a narrow range, or exactly when it is 0.
    """
    hidden = [code for code, metric in first_actions.items() if not isinstance(metric['numerator'], int)]
    if not hidden:
        return
    slack = sum(counts[code] - (minimum if first_actions[code]['numerator'] == 'suppressed' else 0)
                for code in hidden)
    for count, code in sorted((counts[code], code) for code in first_actions if code not in hidden):
        _suppress_numerator(first_actions[code])
        slack += count - minimum
        if slack >= minimum:
            break


def _record_action(snapshot, uid, at, code, *, participation=False):
    row = snapshot.get(uid)
    # Keep the initial population snapshot; ignore users who become eligible later.
    if row is None:
        return
    if not row['joined'] <= at < row['end']:
        return
    # Equal timestamp/code Events differ only by pk; their compact rows
    # have identical effects. An authoritative participation wins any tie.
    candidate = (at, 0 if participation else 1, code)
    first = row['first']
    if first is None or candidate < first:
        row['first'] = candidate
    day = (at.astimezone(row['tz']).date() - row['signup_day']).days
    if 0 <= day <= 30:
        row['days'].add(day)


def build_report(as_of, min_cell=10):
    """as_of is an exclusive UTC midnight; signup weeks are Monday-based UTC.

    Stream compact action rows in a fixed number of queries, then aggregate
    per-user local days in Python. No user-level data leaves this function.
    """
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return _build_report(as_of, min_cell)


def _build_report(as_of, min_cell):
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

    grouped = defaultdict(lambda: defaultdict(int))
    # Profiles are validated with pytz, which ignores case; ZoneInfo does not,
    # and a tzdata folder name such as America raises IsADirectoryError.
    zones = {name.lower(): name for name in available_timezones()}
    snapshot = {}
    local_end_dates = {}
    activation_cutoffs = {name: cutoff - timedelta(days=days) for name, days in ACTIVATION.items()}
    for uid, joined, stored_tz in users.order_by().values_list(
            'pk', 'date_joined', 'profile__timezone').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
        key = zones.get(stored_tz.lower()) if stored_tz else None
        fallback = key in (None, 'UTC')
        tz = ZoneInfo(FALLBACK if fallback else key)
        signup_day = joined.astimezone(tz).date()
        window_end = min(end, datetime.combine(
            signup_day + timedelta(days=31), time.min, tzinfo=tz).astimezone(timezone.utc))
        if tz not in local_end_dates:
            local_end_dates[tz] = end.astimezone(tz).date()
        snapshot[uid] = {'joined': joined, 'tz': tz, 'fallback': fallback,
                         'signup_day': signup_day, 'end': window_end,
                         'first': None, 'days': set()}

    if snapshot:
        events = analytics_events().filter(
            event_type__code__in=MEANINGFUL_EVENT_CODES - {EventType.USER_JOINED_FAST},
            timestamp__gte=F('user__date_joined'),
            timestamp__lt=F('user__date_joined') + timedelta(days=32)).filter(timestamp__lt=end)
        for uid, at, code in events.order_by().values_list(
                'user_id', 'timestamp', 'event_type__code').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
            _record_action(snapshot, uid, at, code)
        # Keep legacy joins separate: an IN subquery inside OR prevents a
        # PostgreSQL semi-join and can produce a costly hashed SubPlan.
        legacy = legacy_transition_events(EventType.USER_JOINED_FAST, dated_joins_only=True).filter(
            timestamp__gte=F('user__date_joined'),
            timestamp__lt=F('user__date_joined') + timedelta(days=32)).filter(timestamp__lt=end)
        for uid, at in legacy.order_by().values_list(
                'user_id', 'timestamp').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
            _record_action(snapshot, uid, at, EventType.USER_JOINED_FAST)
        periods = FastParticipation.objects.filter(
            joined_at__gte=F('profile__user__date_joined'),
            joined_at__lt=F('profile__user__date_joined') + timedelta(days=32)).filter(joined_at__lt=end)
        for uid, at in periods.order_by().values_list(
                'profile__user_id', 'joined_at').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
            _record_action(snapshot, uid, at, EventType.USER_JOINED_FAST, participation=True)

    for row in snapshot.values():
        joined, first = row['joined'], row['first']
        signup_utc = joined.astimezone(timezone.utc).date()
        week = signup_utc - timedelta(days=signup_utc.weekday())
        counts = grouped[week.isoformat()]
        counts['population'] += 1
        counts['fallback'] += row['fallback']
        for name, days in ACTIVATION.items():
            mature = joined <= activation_cutoffs[name]
            hit = first is not None and first[0] <= joined + timedelta(days=days)
            counts[name + '_d'] += mature
            counts[name + '_n'] += mature and hit
            if name == 'activation_7d' and mature and hit:
                counts['first_' + first[2]] += 1
        for name, (start, stop) in RETENTION.items():
            closed = local_end_dates[row['tz']] - timedelta(days=stop + 1)
            mature = row['signup_day'] <= closed
            counts[name + '_d'] += mature
            counts[name + '_n'] += mature and any(start <= day <= stop for day in row['days'])

    cohorts = []
    for week, counts in sorted(grouped.items()):
        population = counts['population']
        metrics = {name: _metric(counts[name + '_n'], counts[name + '_d'],
                                population - counts[name + '_d'], min_cell)
                   for name in (*ACTIVATION, *RETENTION)}
        _suppress_nested(metrics, min_cell)
        first_actions = {
            code: _metric(counts['first_' + code], counts['activation_7d_n'],
                          population - counts['activation_7d_d'], min_cell)
            for code in sorted(MEANINGFUL_EVENT_CODES)}
        # Same immature users as activation_7d; mirror its cell, which was
        # suppressed against that metric's mature denominator.
        for metric in first_actions.values():
            metric['immature'] = metrics['activation_7d']['immature']
        # This distribution repeats the activation numerator as its denominator.
        # Its component counts must not reconstruct a hidden activation count.
        if not isinstance(metrics['activation_7d']['numerator'], int):
            for metric in first_actions.values():
                for key in ('numerator', 'mature_denominator'):
                    if isinstance(metric[key], int):
                        metric[key] = 'suppressed'
                metric.update(percent=None, wilson_95=None)
        _suppress_distribution(first_actions, {code: counts['first_' + code] for code in first_actions}, min_cell)
        cohorts.append({'week_start': week, 'population': _cell(population, min_cell),
                        'timezone_fallback': _metric(counts['fallback'], population, None, min_cell),
                        'metrics': metrics, 'first_action_within_7d': first_actions})
    freshness = analytics_events().filter(timestamp__lt=end).aggregate(latest=Max('timestamp'))['latest']
    # One row's exact timestamp could identify its author; the hour is enough.
    freshness = freshness and freshness.replace(minute=0, second=0, microsecond=0)
    return {'as_of': end.isoformat(), 'activation_data_through': cutoff.isoformat(),
            'data_freshness': freshness.isoformat() if freshness else None,
            'min_cell': min_cell, 'cohort_timezone': 'UTC', 'exclusions_sequential': excluded,
            'caveats': CAVEATS, 'cohorts': cohorts}
