"""Read-only, bounded notification cohorts; identifiers never leave this module."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q

from events.event_schema import (
    NOTIFICATION_DESTINATIONS, NOTIFICATION_DESTINATION_RESULTS, NOTIFICATION_OPEN_ENTRIES,
    NOTIFICATION_PROMPT_CHOICES, NOTIFICATION_PROMPT_TRIGGERS, NOTIFICATION_REPORTING_GROUPS,
)
from events.growth_cohorts import ITERATOR_CHUNK_SIZE, _configured, _metric
from events.models import Event, EventType
from events.participation_analytics import analytics_events, legacy_transition_events
from hub.models import FastParticipation
from notifications.utils import EXPO_ERROR_CODES

MAX_LIFECYCLE_ROWS = 500_000

PRAYER_CODES = {EventType.PRAYER_SET_VIEWED, EventType.DEVOTIONAL_VIEWED,
                EventType.PRAYER_REQUEST_ACCEPTED}
CAVEATS = [
    "Positive event cells require both count and distinct eligible contributors >= k; identifiers stay internal.",
    "Kinds below either attempt floor are pooled into other_kinds before disclosure protection.",
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


class _DisclosureGraph:
    """Canonical counts and declarative (total, parts) relations.

    Aliases (not numerical coincidences) share a node, including denominators.
    Whole-relation suppression deliberately sacrifices utility: even multiple
    hidden parts can be recovered by combining intersecting released equations.
    No equation touching a hidden node is released, so that attack has no inputs.
    Internal residuals are counts too, even though they are never serialized.
    """

    def __init__(self, minimum):
        self.minimum = minimum
        self.values = {}
        self.relations = []
        self.hidden = set()
        self.bindings = []
        self.contributors = {}

    def node(self, name, value, contributors=None):
        if name in self.values:
            if self.values[name] != value:
                raise RuntimeError(f"Inconsistent disclosure node: {name}")
        if contributors is not None:
            self.contributors[name] = contributors
            if value > 0 and len({uid for uid, _ in contributors}) < self.minimum:
                self.hidden.add(name)
        self.values[name] = value
        return name

    def partition(self, total, parts):
        parts = tuple(parts)
        if self.values[total] != sum(self.values[p] for p in parts):
            raise RuntimeError(f"Invalid disclosure partition: {total}")
        self.relations.append((total, parts))

    def subset(self, total, part, name):
        residual = self.node(name, self.values[total] - self.values[part])
        if self.values[residual] < 0:
            raise RuntimeError(f"Negative disclosure residual: {name}")
        if total in self.contributors and part in self.contributors:
            self.node(name, self.values[residual], self.contributors[total] - self.contributors[part])
        self.partition(total, (part, residual))

    def bind(self, cell, field, node):
        self.bindings.append((cell, field, node))

    def finish(self):
        # Zero is a public structural count unless connected to a hidden count.
        # Percentages still require numerator, denominator and complement >= k.
        # Restoring canonical zeros avoids treating an absent kind as a secret
        # and thereby suppressing every partition in an otherwise safe report.
        self.hidden.update(n for n, v in self.values.items() if 0 < v < self.minimum)
        changed = True
        while changed:
            before = len(self.hidden)
            for total, parts in self.relations:
                nodes = {total, *parts}
                if nodes & self.hidden:
                    self.hidden.update(nodes)
            changed = len(self.hidden) != before
        for cell, field, node in self.bindings:
            cell[field] = 'suppressed' if node in self.hidden else self.values[node]
            if node in self.hidden and 'percent' in cell:
                cell.update(percent=None, wilson_95=None)


def _protect_output(output, prompts, counters, prompt_users, open_populations, associated, minimum, contributors):
    """Build the single relation list over the finished report, then suppress."""
    graph = _DisclosureGraph(minimum)
    groups = list(output)

    def count(group, key):
        return graph.node((group, key), counters[group][key], contributors[(group, key)])

    # Every event count, including internal totals, partitions across kinds.
    keys = set().union(*(set(c) for c in counters.values()))
    keys.update(key for metrics in output.values() for key in metrics
                if not key.startswith('associated_'))
    keys.update(('destination_events', 'all_opens', 'send_failures'))
    for group in groups:
        counters[group]['all_opens'] = counters[group]['matched_opens'] + counters[group]['unmatched_opens']
        contributors[(group, 'all_opens')] = contributors[(group, 'matched_opens')] | contributors[(group, 'unmatched_opens')]
        contributors[(group, 'send_failures')] = contributors[(group, 'provider_rejected')] | contributors[(group, 'send_error')]
        counters[group]['send_failures'] = counters[group]['provider_rejected'] + counters[group]['send_error']
    for key in sorted(keys):
        graph.partition(count('overall', key), [count(g, key) for g in groups if g != 'overall'])
    for group, metrics in output.items():
        # Exhaustive categories have an internal remainder for legacy/malformed
        # rows with no recognized category. It must receive primary suppression.
        partitions = [
            ('attempted_tokens', ('provider_accepted', 'provider_rejected', 'send_error')),
            ('send_failures', ('provider_rejected', 'send_error')),
            ('receipts_received', tuple(k for k in metrics if k.startswith('receipt:'))),
            ('send_failures', tuple(k for k in metrics if k.startswith('send_error:'))),
            ('receipt:error', tuple(k for k in metrics if k.startswith('receipt_error:'))),
            ('all_opens', ('matched_opens', 'unmatched_opens')),
            ('all_opens', tuple(k for k in metrics if k.startswith('open:'))),
            ('destination_events', tuple(k for k in metrics if k.startswith('destination:'))),
        ]
        for index, (total, parts) in enumerate(partitions):
            remainder = graph.node((group, 'remainder', index),
                                   counters[group][total] - sum(counters[group][p] for p in parts))
            if graph.values[remainder] < 0:
                raise RuntimeError("Negative category remainder")
            graph.node(remainder, graph.values[remainder], contributors[(group, total)] -
                       set().union(*(contributors[(group, p)] for p in parts)))
            graph.partition(count(group, total), [*(count(group, p) for p in parts), remainder])
        graph.subset(count(group, 'provider_accepted'), count(group, 'accepted_tickets'),
                     (group, 'accepted_without_ticket'))
        graph.subset(count(group, 'matched_opens'), count(group, 'opens_missing_session'),
                     (group, 'sessions_present'))
        for key, cell in metrics.items():
            if key.startswith('associated_'):
                action = key.removeprefix('associated_').removesuffix('_users')
                numerator = graph.node((group, key), len(associated[(group, action)]))
                denominator = graph.node((group, 'open_users'), open_populations[group])
            else:
                numerator = count(group, key)
                denominator = count(group, _denominator(key)) if _denominator(key) else None
            if 'count' in cell:
                graph.bind(cell, 'count', numerator)
            else:
                graph.bind(cell, 'numerator', numerator)
                graph.bind(cell, 'mature_denominator', denominator)
                graph.subset(denominator, numerator, (group, key, 'complement'))
    # Unique users can occur in several kinds: use subset/complement relations,
    # never incorrectly claim that those populations sum to overall.
    for key in ('open_users', 'associated_prayer_users', 'associated_fast_join_users'):
        for group in groups:
            if group != 'overall':
                graph.subset(('overall', key), (group, key), (group, key, 'outside_kind'))
    # Distinct prompt users can select multiple choices; choices are NOT an
    # additive partition. Each row is a subset of its own eligible population.
    for trigger in sorted(NOTIFICATION_PROMPT_TRIGGERS):
        for stage, choices in NOTIFICATION_PROMPT_CHOICES.items():
            for choice in choices:
                key = f'{trigger}:{stage}:{choice}'
                node = graph.node(('prompt', key), len(prompt_users[(trigger, stage, choice)]))
                cell = prompts[key]
                if stage == 'shown':
                    graph.bind(cell, 'count', node)
                else:
                    parent = f'{trigger}:selected:enable' if stage == 'os_result' else f'{trigger}:shown:None'
                    parent_stage, parent_choice = ('selected', 'enable') if stage == 'os_result' else ('shown', None)
                    total = graph.node(('prompt', parent), len(prompt_users[(trigger, parent_stage, parent_choice)]))
                    graph.bind(cell, 'numerator', node)
                    graph.bind(cell, 'mature_denominator', total)
                    graph.subset(total, node, ('prompt', key, 'complement'))
        key = f'{trigger}:unmatched'
        graph.bind(prompts[key], 'count', graph.node(('prompt', key), len(prompt_users[(trigger, 'unmatched', None)])))
    graph.finish()
    return graph


def _denominator(key):
    if key in {'provider_accepted', 'provider_rejected', 'send_error', 'accepted_tickets'} or key.startswith('send_error:'):
        return 'attempted_tokens'
    if key == 'receipts_received':
        return 'accepted_tickets'
    if key.startswith(('receipt:', 'receipt_error:')):
        return 'receipts_received'
    if key.startswith('destination:'):
        return 'destination_events'
    return None


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
    if events.count() > MAX_LIFECYCLE_ROWS:
        raise ValueError(f'Notification funnel exceeds {MAX_LIFECYCLE_ROWS:,} lifecycle rows; reduce --since-days.')
    rows = list(events.order_by('timestamp', 'pk').values_list(
        'event_type__code', 'user_id', 'timestamp', 'data'))
    counters = defaultdict(Counter)
    sends, tickets, opens = {}, {}, defaultdict(list)
    groups = ['overall', *sorted(set(NOTIFICATION_REPORTING_GROUPS.values()))]

    contributors = defaultdict(set)
    freshness = None
    attempt_users = defaultdict(set)
    attempt_counts = Counter()
    for code, uid, at, data in rows:
        if code == EventType.NOTIFICATION_SEND:
            group = NOTIFICATION_REPORTING_GROUPS.get(data.get('kind'), 'unclassified')
            attempt_counts[group] += 1
            attempt_users[group].add(uid)
    folded = {g for g in attempt_counts if attempt_counts[g] < minimum or len(attempt_users[g]) < minimum}
    groups = [g for g in groups if g not in folded]
    if folded:
        groups.append('other_kinds')

    def add(group, key, count=1):
        contributors[('overall', key)].add((uid, row_id))
        if group != 'overall':
            contributors[(group, key)].add((uid, row_id))
        counters['overall'][key] += count
        if group != 'overall':
            counters[group][key] += count

    for row_id, (code, uid, at, data) in enumerate(rows):
        if code != EventType.NOTIFICATION_SEND:
            continue
        group = NOTIFICATION_REPORTING_GROUPS.get(data.get('kind'), 'unclassified')
        group = 'other_kinds' if group in folded else group
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
            tickets[(nid, data['ticket_id'])] = (group, at, uid, row_id)
            add(group, 'accepted_tickets')
    prompt_users = defaultdict(set)
    prompt_state = defaultdict(set)
    seen_receipts = set()
    for row_id, (code, uid, at, data) in enumerate(rows):
        if code != EventType.NOTIFICATION_RECEIPT:
            freshness = max(freshness, at) if freshness else at
        if code == EventType.NOTIFICATION_RECEIPT:
            key = (data.get('notif_id'), data.get('ticket_id'))
            send = tickets.get(key)
            if send and send[1] <= at and key not in seen_receipts and data.get('receipt_status') in {'ok', 'error'}:
                seen_receipts.add(key)
                group, _, uid, row_id = send
                freshness = max(freshness, at) if freshness else at
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
                opens[uid].append((at, data.get('session_id'), group))
                if not data.get('session_id'):
                    add(group, 'opens_missing_session')
            else:
                group = 'other_kinds' if 'unclassified' in folded else 'unclassified'
                add(group, 'unmatched_opens')
            entry = data.get('entry')
            if entry in NOTIFICATION_OPEN_ENTRIES:
                add(group, 'open:' + entry)
        elif code == EventType.NOTIFICATION_DESTINATION:
            send = sends.get((uid, data.get('notif_id')))
            group = send[0] if send and send[1] <= at else (
                'other_kinds' if 'unclassified' in folded else 'unclassified')
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
    open_populations = {}
    for group in groups:
        counts = counters[group]
        metrics = {}
        for key in keys:
            denominator = _denominator(key)
            # Raw opens deliberately have no send denominator or percentage.
            if denominator is None or key == 'attempted_tokens':
                metrics[key] = {'count': counts[key] if counts[key] >= minimum else 'suppressed'}
            else:
                metrics[key] = _release(counts[key], counts[denominator], minimum)
        open_users = {uid for uid, entries in opens.items() if group == 'overall'
                      or any(entry[2] == group for entry in entries)}
        open_populations[group] = len(open_users)
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
    _protect_output(output, prompts, counters, prompt_users, open_populations, associated, minimum, contributors)
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
