"""Read-only weekly growth cohorts. Only aggregate cells leave this module."""

import math
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

from decouple import Csv, config
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Max, Q

from events.event_schema import MEANINGFUL_EVENT_CODES
from events.models import Event, EventType
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


def build_report(as_of, min_cell=10):
    """as_of is an exclusive UTC midnight; signup weeks are Monday-based UTC.

    Stream compact action rows in a fixed number of queries, then aggregate
    per-user local days in Python. No user-level data leaves this function.
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

    grouped = defaultdict(lambda: defaultdict(int))
    # Profiles are validated with pytz, which ignores case; ZoneInfo does not.
    zones = {name.lower(): name for name in available_timezones()}
    population = {}
    for uid, joined, stored_tz in users.order_by().values_list(
            'pk', 'date_joined', 'profile__timezone').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
        key = zones.get(stored_tz.lower()) if stored_tz else None
        fallback = key in (None, 'UTC')
        tz = ZoneInfo(FALLBACK if fallback else key)
        signup_day = joined.astimezone(tz).date()
        # Day 30 can end after 31 elapsed days across a fall DST transition.
        # Preserve that tail from the original local-calendar retention query.
        window_end = min(end, max(joined + timedelta(days=31), datetime.combine(
            signup_day + timedelta(days=31), time.min, tzinfo=tz).astimezone(timezone.utc)))
        population[uid] = {'joined': joined, 'tz': tz, 'fallback': fallback,
                           'signup_day': signup_day, 'end': window_end,
                           'first': None, 'days': set()}

    def record(uid, at, code, *, participation=False):
        row = population[uid]
        if not row['joined'] <= at < row['end']:
            return
        # Equal timestamp/code Events differ only by pk; their compact rows
        # have identical effects. An authoritative participation wins any tie.
        candidate = (at, code)
        first = row['first']
        if first is None or (at <= first[0] if participation else candidate < first):
            row['first'] = candidate
        day = (at.astimezone(row['tz']).date() - row['signup_day']).days
        if 0 <= day <= 30:
            row['days'].add(day)

    if population:
        earliest = min(row['joined'] for row in population.values())
        latest = max(row['end'] for row in population.values())
        # An uncorrelated population subquery avoids an unbounded SQL parameter
        # list and keeps query count independent of population size.
        eligible_ids = users.order_by().values('pk')
        events = analytics_events().filter(
            event_type__code__in=MEANINGFUL_EVENT_CODES - {EventType.USER_JOINED_FAST},
            user_id__in=eligible_ids, timestamp__gte=earliest, timestamp__lt=latest)
        for uid, at, code in events.order_by().values_list(
                'user_id', 'timestamp', 'event_type__code').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
            record(uid, at, code)
        # Keep legacy joins separate: an IN subquery inside OR prevents a
        # PostgreSQL semi-join and can produce a costly hashed SubPlan.
        legacy = legacy_transition_events(EventType.USER_JOINED_FAST, dated_joins_only=True).filter(
            user_id__in=eligible_ids, timestamp__gte=earliest, timestamp__lt=latest)
        for uid, at in legacy.order_by().values_list(
                'user_id', 'timestamp').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
            record(uid, at, EventType.USER_JOINED_FAST, participation=False)
        periods = FastParticipation.objects.filter(
            profile__user_id__in=eligible_ids, joined_at__gte=earliest, joined_at__lt=latest)
        for uid, at in periods.order_by().values_list(
                'profile__user_id', 'joined_at').iterator(chunk_size=ITERATOR_CHUNK_SIZE):
            record(uid, at, EventType.USER_JOINED_FAST, participation=True)

    for row in population.values():
        joined, first = row['joined'], row['first']
        signup_utc = joined.astimezone(timezone.utc).date()
        week = signup_utc - timedelta(days=signup_utc.weekday())
        counts = grouped[week.isoformat()]
        counts['population'] += 1
        counts['fallback'] += row['fallback']
        for name, days in ACTIVATION.items():
            mature = joined <= cutoff - timedelta(days=days)
            hit = first is not None and first[0] <= joined + timedelta(days=days)
            counts[name + '_d'] += mature
            counts[name + '_n'] += mature and hit
            if name == 'activation_7d' and mature and hit:
                counts['first_' + first[1]] += 1
        for name, (start, stop) in RETENTION.items():
            closed = end.astimezone(row['tz']).date() - timedelta(days=stop + 1)
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
    freshness = Event.objects.filter(timestamp__lt=end).aggregate(latest=Max('timestamp'))['latest']
    # One row's exact timestamp could identify its author; the hour is enough.
    freshness = freshness and freshness.replace(minute=0, second=0, microsecond=0)
    return {'as_of': end.isoformat(), 'activation_data_through': cutoff.isoformat(),
            'data_freshness': freshness.isoformat() if freshness else None,
            'min_cell': min_cell, 'cohort_timezone': 'UTC', 'exclusions_sequential': excluded,
            'caveats': CAVEATS, 'cohorts': cohorts}
