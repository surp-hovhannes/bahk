"""Read-only, bounded notification cohorts; identifiers never leave this module."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Exists, Max, OuterRef, Q

from events.event_schema import (
    NOTIFICATION_DESTINATIONS, NOTIFICATION_DESTINATION_RESULTS, NOTIFICATION_OPEN_ENTRIES,
    NOTIFICATION_PROMPT_CHOICES, NOTIFICATION_PROMPT_TRIGGERS, NOTIFICATION_REPORTING_GROUPS,
)
from events.growth_cohorts import ITERATOR_CHUNK_SIZE, _configured, _metric
from events.models import Event, EventType
from events.participation_analytics import analytics_events, legacy_transition_events
from hub.models import FastParticipation
from notifications.utils import EXPO_ERROR_CODES

PRAYER_CODES = {EventType.PRAYER_SET_VIEWED, EventType.DEVOTIONAL_VIEWED,
                EventType.PRAYER_REQUEST_ACCEPTED}
CAVEATS = [
    'Provider ticket acceptance means accepted by provider; device delivery is not measured.',
    'Receipt ok means handed to the platform push service per Expo receipt; not seen on the device.',
    'Receipt coverage can be incomplete because of ledger capacity, expiry, or disabled telemetry.',
    'Opens are client-reported, OTA-gated and logged-in only; eligible client denominator is not derivable. '
    'Open rate is a lower bound but is not calculated against all sends.',
    'Attribution is association, not causation; first means first recorded action in the combined group. '
    'Missing open session IDs allow time-only association; participation rows have no session ID.',
    'Do not publish different cutoffs or overlapping reports side by side without privacy review.',
]


def _release(n, d, minimum):
    """Reuse Wilson/complement rules; pair suppression also hides the denominator."""
    cell = _metric(n, d, None, minimum)
    if not all(isinstance(cell[key], int) for key in ('numerator', 'mature_denominator')):
        cell.update(numerator='suppressed', mature_denominator='suppressed', percent=None, wilson_95=None)
    return cell


def build_report(as_of, since_days=30, min_cell=10):
    if not 1 <= since_days <= 400:
        raise ValueError('since_days must be between 1 and 400')
    if min_cell < 10:
        raise ValueError('min_cell must be at least 10')
    if connection.in_atomic_block:
        return _build_report(as_of, since_days, min_cell)
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return _build_report(as_of, since_days, min_cell)


def _build_report(as_of, since_days, minimum):
    end = datetime.combine(as_of, time.min, tzinfo=timezone.utc)
    start = end - timedelta(days=since_days)
    users = get_user_model().objects.filter(date_joined__lt=end, is_staff=False, is_superuser=False)
    ids = _configured('ANALYTICS_EXCLUDED_USER_IDS')
    if any(not v.isdecimal() for v in ids):
        raise ValueError('ANALYTICS_EXCLUDED_USER_IDS must contain integer IDs')
    users = users.exclude(pk__in=[int(v) for v in ids])
    for domain in _configured('ANALYTICS_EXCLUDED_EMAIL_DOMAINS'):
        users = users.exclude(email__iendswith='@' + domain.lstrip('@'))
    eligible = Q(user_id__in=users.values('pk'))
    # All database reads are type/time bounded. JSON correlation is in memory,
    # never an unindexed full-table JSON predicate. Existing type/time index fits.
    events = Event.objects.filter(event_type__code__in=EventType.NOTIFICATION_LIFECYCLE_CODES,
                                  timestamp__gte=start, timestamp__lt=end)
    events = events.filter(eligible | Q(user__isnull=True, event_type__code=EventType.NOTIFICATION_RECEIPT))
    rows = list(events.order_by('timestamp', 'pk').values_list(
        'event_type__code', 'user_id', 'timestamp', 'data').iterator(chunk_size=ITERATOR_CHUNK_SIZE))
    counters = defaultdict(Counter)
    sends, tickets, opens = {}, {}, defaultdict(list)
    groups = ['overall', *sorted(set(NOTIFICATION_REPORTING_GROUPS.values()))]

    def add(group, key, count=1):
        counters['overall'][key] += count
        if group != 'overall':
            counters[group][key] += count

    for code, uid, at, data in rows:
        if code != EventType.NOTIFICATION_SEND:
            continue
        group = NOTIFICATION_REPORTING_GROUPS.get(data.get('kind'), 'unclassified')
        add(group, 'attempted_tokens')
        outcome = data.get('outcome')
        if outcome in {'provider_accepted', 'provider_rejected', 'send_error'}:
            add(group, outcome)
        if outcome in {'provider_rejected', 'send_error'}:
            error = data.get('error_code')
            add(group, 'send_error:' + (error if error in EXPO_ERROR_CODES else 'other'))
        nid = data.get('notif_id')
        if nid and uid:
            sends[(uid, nid)] = (group, at)
        if nid and data.get('ticket_id') and outcome == 'provider_accepted':
            tickets[(nid, data['ticket_id'])] = (group, at)
            add(group, 'accepted_tickets')
    prompt_users = defaultdict(set)
    prompt_state = defaultdict(set)
    seen_receipts = set()
    for code, uid, at, data in rows:
        if code == EventType.NOTIFICATION_RECEIPT:
            key = (data.get('notif_id'), data.get('ticket_id'))
            send = tickets.get(key)
            if send and send[1] <= at and key not in seen_receipts and data.get('receipt_status') in {'ok', 'error'}:
                seen_receipts.add(key)
                group = send[0]
                add(group, 'receipts_received')
                add(group, 'receipt:' + data['receipt_status'])
                if data['receipt_status'] == 'error':
                    error = data.get('error_code')
                    add(group, 'receipt_error:' + (error if error in EXPO_ERROR_CODES else 'other'))
        elif code == EventType.NOTIFICATION_OPENED:
            send = sends.get((uid, data.get('notif_id')))
            if send and send[1] <= at:
                group = send[0]
                add(group, 'matched_opens')
                entry = data.get('entry')
                if entry in NOTIFICATION_OPEN_ENTRIES:
                    add(group, 'open:' + entry)
                opens[uid].append((at, data.get('session_id'), group))
                if not data.get('session_id'):
                    add(group, 'opens_missing_session')
            else:
                add('unclassified', 'unmatched_opens')
        elif code == EventType.NOTIFICATION_DESTINATION:
            send = sends.get((uid, data.get('notif_id')))
            group = send[0] if send and send[1] <= at else 'unclassified'
            dest, result = data.get('destination'), data.get('result')
            if dest in NOTIFICATION_DESTINATIONS and result in NOTIFICATION_DESTINATION_RESULTS:
                add(group, f'destination:{dest}:{result}')
                add(group, 'destination_events')
        elif code == EventType.NOTIFICATION_PROMPT:
            trigger, stage, choice = data.get('trigger'), data.get('stage'), data.get('choice')
            if trigger in NOTIFICATION_PROMPT_TRIGGERS and choice in NOTIFICATION_PROMPT_CHOICES.get(stage, ()):
                state = prompt_state[(uid, trigger)]
                if stage == 'shown' or (stage == 'selected' and 'shown' in state) or (
                        stage == 'os_result' and 'enable' in state):
                    prompt_users[(trigger, stage, choice)].add(uid)
                    state.add('enable' if stage == 'selected' and choice == 'enable' else stage)
                else:
                    prompt_users[(trigger, 'unmatched', None)].add(uid)

    associated = defaultdict(set)

    def associate(uid, at, session, action):
        for opened_at, opened_session, group in opens.get(uid, ()):
            if opened_at <= at <= opened_at + timedelta(minutes=30):
                if opened_session and session != opened_session:
                    continue
                associated[(group, action)].add(uid)
                associated[('overall', action)].add(uid)

    if opens:
        open_user_ids = events.filter(event_type__code=EventType.NOTIFICATION_OPENED).values('user_id')
        actions = analytics_events().filter(user_id__in=open_user_ids, timestamp__gte=start, timestamp__lt=end,
                                            event_type__code__in=PRAYER_CODES)
        earlier = analytics_events().filter(user_id=OuterRef('user_id'),
                                            event_type__code__in=PRAYER_CODES,
                                            timestamp__lt=OuterRef('timestamp'))
        actions = actions.annotate(has_prior=Exists(earlier)).filter(has_prior=False)
        for uid, at, data in actions.order_by().values_list('user_id', 'timestamp', 'data').iterator(
                chunk_size=ITERATOR_CHUNK_SIZE):
            associate(uid, at, data.get('session_id'), 'prayer')
        joins = legacy_transition_events(EventType.USER_JOINED_FAST, dated_joins_only=True).filter(
            user_id__in=open_user_ids, timestamp__gte=start, timestamp__lt=end)
        prior_join = legacy_transition_events(EventType.USER_JOINED_FAST, dated_joins_only=True).filter(
            user_id=OuterRef('user_id'), timestamp__lt=OuterRef('timestamp'))
        prior_period = FastParticipation.objects.filter(profile__user_id=OuterRef('user_id'),
                                                       joined_at__lt=OuterRef('timestamp'))
        joins = joins.annotate(prior_event=Exists(prior_join), prior_period=Exists(prior_period)).filter(
            prior_event=False, prior_period=False)
        for uid, at, data in joins.order_by().values_list('user_id', 'timestamp', 'data').iterator(
                chunk_size=ITERATOR_CHUNK_SIZE):
            associate(uid, at, data.get('session_id'), 'fast_join')
        periods = FastParticipation.objects.filter(profile__user_id__in=open_user_ids,
                                                   joined_at__gte=start, joined_at__lt=end)
        prior_period = FastParticipation.objects.filter(profile__user_id=OuterRef('profile__user_id'),
                                                       joined_at__lt=OuterRef('joined_at'))
        prior_join = legacy_transition_events(EventType.USER_JOINED_FAST, dated_joins_only=True).filter(
            user_id=OuterRef('profile__user_id'), timestamp__lt=OuterRef('joined_at'))
        periods = periods.annotate(prior_event=Exists(prior_join), prior_period=Exists(prior_period)).filter(
            prior_event=False, prior_period=False)
        for uid, at in periods.order_by().values_list('profile__user_id', 'joined_at').iterator(
                chunk_size=ITERATOR_CHUNK_SIZE):
            associate(uid, at, None, 'fast_join')
    denominators = {'attempted_tokens': 'attempted_tokens', 'provider_accepted': 'attempted_tokens',
                    'provider_rejected': 'attempted_tokens', 'send_error': 'attempted_tokens',
                    'accepted_tickets': 'attempted_tokens', 'receipts_received': 'accepted_tickets',
                    'matched_opens': None, 'unmatched_opens': None, 'opens_missing_session': None}
    keys = list(denominators)
    keys += ['send_error:' + e for e in sorted(EXPO_ERROR_CODES | {'other'})]
    keys += ['receipt:' + s for s in ('ok', 'error')]
    keys += ['receipt_error:' + e for e in sorted(EXPO_ERROR_CODES | {'other'})]
    keys += ['open:' + entry for entry in sorted(NOTIFICATION_OPEN_ENTRIES)]
    keys += [f'destination:{d}:{r}' for d in sorted(NOTIFICATION_DESTINATIONS)
             for r in sorted(NOTIFICATION_DESTINATION_RESULTS)]
    output = {}
    metric_denominators = {}
    for group in groups:
        counts = counters[group]
        metrics = {}
        for key in keys:
            denominator = denominators.get(key)
            if key.startswith('send_error:'):
                denominator = 'attempted_tokens'
            elif key.startswith('receipt:') or key.startswith('receipt_error:'):
                denominator = 'receipts_received'
            elif key.startswith('destination:'):
                denominator = 'destination_events'
            metric_denominators[key] = denominator
            # Raw opens deliberately have no send denominator or percentage.
            if denominator is None or key == 'attempted_tokens':
                metrics[key] = {'count': counts[key] if counts[key] >= minimum else 'suppressed'}
            else:
                metrics[key] = _release(counts[key], counts[denominator], minimum)
        open_users = {uid for uid, entries in opens.items() if group == 'overall'
                      or any(entry[2] == group for entry in entries)}
        for action in ('prayer', 'fast_join'):
            metrics['associated_' + action + '_users'] = _release(
                len(associated[(group, action)]), len(open_users), minimum)
        output[group] = metrics
    prompts = {}
    for trigger in sorted(NOTIFICATION_PROMPT_TRIGGERS):
        shown = prompt_users[(trigger, 'shown', None)]
        enabled = prompt_users[(trigger, 'selected', 'enable')]
        for stage, choices in NOTIFICATION_PROMPT_CHOICES.items():
            for choice in choices:
                denominator = enabled if stage == 'os_result' else shown
                count = len(prompt_users[(trigger, stage, choice)])
                prompts[f'{trigger}:{stage}:{choice}'] = (
                    {'count': count if count >= minimum else 'suppressed'} if stage == 'shown'
                    else _release(count, len(denominator), minimum))
        unmatched = len(prompt_users[(trigger, 'unmatched', None)])
        prompts[f'{trigger}:unmatched'] = {'count': unmatched if unmatched >= minimum else 'suppressed'}
    # Conservative complementary suppression: if any component is hidden, hide
    # the entire additive partition, including its total. This also protects zeros.
    def hide(cell):
        for key in ('count', 'numerator', 'mature_denominator'):
            if key in cell:
                cell[key] = 'suppressed'
        if 'percent' in cell:
            cell.update(percent=None, wilson_95=None)

    for key in keys:
        cells = [output[group][key] for group in groups]
        if any(0 < counters[group][key] < minimum for group in groups[1:]):
            for cell in cells:
                hide(cell)
    for group, metrics in output.items():
        outcome_keys = ('provider_accepted', 'provider_rejected', 'send_error')
        if any(0 < counters[group][key] < minimum for key in outcome_keys):
            for key in (*outcome_keys, 'attempted_tokens'):
                hide(metrics[key])
        for prefix in ('send_error:', 'receipt:', 'receipt_error:', 'open:', 'destination:'):
            cells = [cell for key, cell in metrics.items() if key.startswith(prefix)]
            if any(0 < counters[group][key] < minimum for key in metrics if key.startswith(prefix)):
                for cell in cells:
                    hide(cell)
                total = {'send_error:': 'attempted_tokens', 'receipt:': 'receipts_received',
                         'receipt_error:': 'receipts_received', 'open:': 'matched_opens'}.get(prefix)
                if total:
                    hide(metrics[total])
    for key in keys:
        if any(counters[group][key] > 0 and
               not isinstance(output[group][key].get('count', output[group][key].get('numerator')), int)
               for group in groups[1:]):
            hide(output['overall'][key])
    # A hidden total must stay hidden wherever it occurs as a denominator.
    # Iterate because hiding a dependent numerator can hide another kind total,
    # whose denominators in turn need suppression (tickets -> receipt coverage).
    changed = True
    while changed:
        before = repr(output)
        for group, metrics in output.items():
            for key, denominator in metric_denominators.items():
                if denominator in metrics:
                    total = metrics[denominator]
                    if not isinstance(total.get('count', total.get('numerator')), int):
                        hide(metrics[key])
        for key in keys:
            if any(counters[group][key] > 0 and not isinstance(
                    output[group][key].get('count', output[group][key].get('numerator')), int)
                    for group in groups[1:]):
                for group in groups:
                    hide(output[group][key])
        changed = before != repr(output)
    # Prompt rows are event distributions, not linked user transitions.
    for trigger in NOTIFICATION_PROMPT_TRIGGERS:
        cells = [cell for key, cell in prompts.items() if key.startswith(trigger + ':')]
        if any(0 < len(prompt_users[(trigger, stage, choice)]) < minimum
               for stage, choices in NOTIFICATION_PROMPT_CHOICES.items() for choice in choices):
            for cell in cells:
                hide(cell)
    freshness = events.aggregate(latest=Max('timestamp'))['latest']
    freshness = freshness and freshness.replace(minute=0, second=0, microsecond=0)
    return {'as_of': end.isoformat(), 'since': start.isoformat(), 'min_cell': minimum,
            'data_freshness': freshness.isoformat() if freshness else None,
            'open_eligible_denominator': 'not derivable',
            'denominators': {'sends': 'attempted tokens', 'receipts_received': 'accepted tickets with ticket_id',
                             'receipt_outcomes': 'matched receipts received',
                             'destinations': 'destination events in the group',
                             'attribution': 'distinct users with correlated opens',
                             'prompt_selected': 'users with preceding shown for this trigger',
                             'prompt_os_result': 'users with preceding selected enable for this trigger'},
            'caveats': CAVEATS, 'groups': output, 'permission_prompts': prompts}
