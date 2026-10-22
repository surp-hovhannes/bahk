"""Synthetic report contracts; no telemetry flag or producer is required."""
import json
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from contextlib import nullcontext
from fractions import Fraction
from unittest.mock import MagicMock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from events import notification_funnel
from events.growth_cohorts import build_report as cohort_report
from events.models import Event, EventType
from events.notification_funnel import _protect_output, _release, build_report
from hub.models import Fast, FastParticipation, Profile
from prayers.models import PrayerSet
from tests.fixtures.test_data import TestDataFactory


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
                   NOTIFICATION_TELEMETRY_ENABLED=False,
                   ANALYTICS_EXCLUDED_USER_IDS=[], ANALYTICS_EXCLUDED_EMAIL_DOMAINS=[])
class NotificationFunnelTests(TestCase):
    def setUp(self):
        self.at = datetime(2026, 3, 10, 10, tzinfo=timezone.utc)
        self.user = get_user_model().objects.create_user(username='notification-report', date_joined=self.at)
        self.types = {}
        for code in EventType.NOTIFICATION_LIFECYCLE_CODES:
            self.types[code], _ = EventType.objects.get_or_create(code=code, defaults={'name': code})

    def event(self, code, user=None, at=None, **data):
        return Event.objects.create(event_type=self.types[code], user=user,
                                    title=EventType.NOTIFICATION_LIFECYCLE_TITLES[code],
                                    timestamp=at or self.at, data=data)

    def person(self, name):
        return get_user_model().objects.get_or_create(username=name, defaults={"date_joined": self.at})[0]

    def report(self):
        return build_report(date(2026, 3, 12))

    def test_empty_and_command(self):
        self.assertIsNone(self.report()['data_freshness'])
        out = StringIO()
        call_command('notification_funnel', as_of='2026-03-12', stdout=out)
        self.assertIn('accepted by provider', out.getvalue())
        self.assertNotIn('delivered', out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['min_cell'], 10)

    def test_pair_thresholds_and_complement(self):
        for n, d in ((0, 20), (9, 30), (10, 19), (20, 20)):
            self.assertEqual(_release(n, d, 10)['mature_denominator'], 'suppressed')
        self.assertEqual(_release(10, 20, 10)['percent'], 50)

    def test_same_user_correlation_receipts_and_staff(self):
        for i in range(20):
            user = self.person(f"same-{i}")
            other = self.person(f"other-{i}")
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=str(i), ticket_id=str(i),
                       kind='daily_fast', outcome='provider_accepted')
            self.event(EventType.NOTIFICATION_OPENED, user if i < 10 else other,
                       at=self.at + timedelta(minutes=1), notif_id=str(i), entry='tap')
            if i < 10:
                self.event(EventType.NOTIFICATION_RECEIPT, at=self.at + timedelta(minutes=2),
                           notif_id=str(i), ticket_id=str(i), receipt_status='ok')
        # Keep the accepted-ticket numerator and its complement above k.
        for i in range(20):
            user = self.person(f"reject-{i}")
            self.event(EventType.NOTIFICATION_SEND, user, kind='daily_fast',
                       outcome='provider_rejected')
        report = self.report()['groups']
        self.assertEqual(report['daily_fast']['matched_opens']['count'], 10)
        self.assertEqual(report['unclassified']['unmatched_opens']['count'], 10)
        self.assertEqual(report['daily_fast']['receipts_received']['percent'], 50)
        get_user_model().objects.exclude(pk=self.user.pk).update(is_staff=True)
        self.assertEqual(self.report()['groups']['daily_fast']['attempted_tokens']['count'], 0)

    def test_freshness_excludes_notification_rows_in_cohorts(self):
        before = cohort_report(date(2026, 3, 12))['data_freshness']
        self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast',
                   at=self.at + timedelta(days=1))
        self.assertEqual(cohort_report(date(2026, 3, 12))['data_freshness'], before)
        self.assertEqual(self.report()['data_freshness'], (self.at + timedelta(days=1)).isoformat())

    def test_prompt_cells_are_suppressed(self):
        self.event(EventType.NOTIFICATION_PROMPT, self.user, stage='selected', choice='enable', trigger='settings')
        cells = self.report()['permission_prompts']
        self.assertEqual(cells['settings:selected:enable']['numerator'], 0)
        self.assertEqual(cells['settings:selected:enable']['mature_denominator'], 0)
        self.assertIsNone(cells['settings:selected:enable']['percent'])
        self.assertEqual(cells['settings:unmatched']['count'], 'suppressed')

    def test_attribution_boundaries_and_sessions(self):
        prayer_type, _ = EventType.objects.get_or_create(
            code=EventType.PRAYER_SET_VIEWED, defaults={'name': 'Prayer', 'requires_target': True})
        prayer_set = PrayerSet.objects.create(
            title='Boundary prayers', church=TestDataFactory.create_church())
        prayer_set_type = ContentType.objects.get_for_model(PrayerSet)
        # Ten at each boundary; ten too late, ten with another session.
        for i in range(40):
            user = get_user_model().objects.create_user(username=f'boundary{i}', date_joined=self.at)
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=str(i), kind='daily_fast',
                       outcome='provider_accepted')
            self.event(EventType.NOTIFICATION_OPENED, user, notif_id=str(i), session_id='session')
            delay = (0 if i < 10 else 30 if i < 20 else 31 if i < 30 else 1)
            Event.objects.create(event_type=prayer_type, user=user, title='Prayer set viewed',
                                 content_type=prayer_set_type, object_id=prayer_set.pk,
                                 timestamp=self.at + timedelta(minutes=delay),
                                 data={'session_id': 'other' if i >= 30 else 'session'})
        metric = self.report()['groups']['daily_fast']['associated_prayer_users']
        self.assertEqual(metric['numerator'], 20)
        self.assertEqual(metric['mature_denominator'], 40)

    def test_prompt_sequential_users(self):
        for i in range(40):
            user = get_user_model().objects.create_user(username=f'prompt{i}', date_joined=self.at)
            self.event(EventType.NOTIFICATION_PROMPT, user, trigger='settings', stage='shown', choice=None)
            if i < 20:
                self.event(EventType.NOTIFICATION_PROMPT, user, trigger='settings', stage='selected', choice='enable')
                self.event(EventType.NOTIFICATION_PROMPT, user, trigger='settings', stage='os_result',
                           choice='granted' if i < 10 else 'denied')
            else:
                self.event(EventType.NOTIFICATION_PROMPT, user, trigger='settings', stage='selected', choice='not_now')
        # Outcomes use the enabled population, not all prompt events.
        cells = self.report()['permission_prompts']
        self.assertEqual(cells['settings:os_result:granted']['percent'], 50)
        self.assertEqual(cells['settings:selected:enable']['mature_denominator'], 40)

    def test_wrong_ticket_and_window_edges(self):
        for i in range(20):
            user = self.person(f"sender-{i}")
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=str(i), ticket_id=str(i),
                       kind='daily_fast', outcome='provider_accepted')
            self.event(EventType.NOTIFICATION_RECEIPT, notif_id=str(i), ticket_id='wrong', receipt_status='ok')
        self.event(EventType.NOTIFICATION_OPENED, user, notif_id='0',
                   at=datetime(2026, 3, 12, tzinfo=timezone.utc))
        report = self.report()['groups']['daily_fast']
        self.assertEqual(report['receipts_received']['numerator'], 0)
        self.assertEqual(report['matched_opens']['count'], 0)
        with self.assertRaises(ValueError):
            build_report(date(2026, 3, 12), since_days=401)
        with self.assertRaises(ValueError):
            build_report(date(2026, 3, 12), min_cell=9)

    def test_40_attempts_plus_five_rejected_kind_hide_dependent_denominators(self):
        for i in range(40):
            user = self.person(f"sender-{i}")
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=str(i), ticket_id=str(i),
                       kind='daily_fast', outcome='provider_accepted' if i < 20 else 'provider_rejected')
            if i < 10:
                self.event(EventType.NOTIFICATION_RECEIPT, notif_id=str(i), ticket_id=str(i),
                           receipt_status='ok')
        for _ in range(5):
            self.event(EventType.NOTIFICATION_SEND, user, kind='unknown', outcome='provider_rejected')
        groups = self.report()['groups']
        self.assertEqual(groups['daily_fast']['attempted_tokens']['count'], 40)
        self.assertEqual(groups['daily_fast']['receipts_received']['percent'], 50)
        for group in ('other_kinds', 'overall'):
            self.assertEqual(groups[group]['attempted_tokens']['count'], 'suppressed')
            for key in ('provider_accepted', 'provider_rejected', 'accepted_tickets', 'receipts_received',
                        'receipt:ok'):
                with self.subTest(group=group, key=key):
                    self.assertEqual(groups[group][key]['numerator'], 'suppressed')
                    self.assertEqual(groups[group][key]['mature_denominator'], 'suppressed')
                    self.assertIsNone(groups[group][key]['percent'])
                    self.assertIsNone(groups[group][key]['wilson_95'])

    def test_entry_breakdown_includes_unmatched_opens(self):
        for i in range(10):
            user = self.person(f"sender-{i}")
            other = self.person(f"other-{i}")
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=f'matched-{i}', kind='daily_fast')
            self.event(EventType.NOTIFICATION_OPENED, user, notif_id=f'matched-{i}', entry='tap')
            # Wrong-user correlation and sends preceding the report window both
            # remain raw opens with a known entry and an unknown kind.
            self.event(EventType.NOTIFICATION_OPENED, other, notif_id=f'matched-{i}', entry='tap')
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=f'old-{i}', kind='daily_fast',
                       at=self.at - timedelta(days=40))
            self.event(EventType.NOTIFICATION_OPENED, user, notif_id=f'old-{i}', entry='cold_start')
        groups = self.report()['groups']
        self.assertEqual(groups['daily_fast']['open:tap']['count'], 10)
        self.assertEqual(groups['unclassified']['unmatched_opens']['count'], 20)
        self.assertEqual(groups['unclassified']['open:tap']['count'], 10)
        self.assertEqual(groups['unclassified']['open:cold_start']['count'], 10)
        self.assertEqual(groups['overall']['open:tap']['count'], 20)
        self.assertEqual(groups['overall']['open:cold_start']['count'], 10)

    def test_sparse_missing_sessions_hide_cross_kind_partition(self):
        for kind, count in (('daily_fast', 15), ('prayer_thanks', 5)):
            for i in range(count):
                user = self.person(f"{kind}-{i}")
                nid = f'{kind}-{i}'
                self.event(EventType.NOTIFICATION_SEND, user, notif_id=nid, kind=kind)
                self.event(EventType.NOTIFICATION_OPENED, user, notif_id=nid, entry='tap')
        groups = self.report()['groups']
        self.assertEqual(groups['daily_fast']['opens_missing_session']['count'], 15)
        for group in ('overall', 'other_kinds'):
            with self.subTest(group=group):
                self.assertEqual(groups[group]['opens_missing_session']['count'], 'suppressed')
        # A fully publishable partition still exposes the correct raw counts.
        for i in range(5):
            user = self.person(f"extra-{i}")
            nid = f'extra-{i}'
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=nid, kind='prayer_thanks')
            self.event(EventType.NOTIFICATION_OPENED, user, notif_id=nid, entry='tap')
        groups = self.report()['groups']
        self.assertEqual(groups['overall']['opens_missing_session']['count'], 25)
        self.assertEqual(groups['daily_fast']['opens_missing_session']['count'], 15)
        self.assertEqual(groups['prayer_request_notifications']['opens_missing_session']['count'], 10)

    def test_first_fast_join_uses_only_eligible_prior_evidence(self):
        join_type, _ = EventType.objects.get_or_create(
            code=EventType.USER_JOINED_FAST, defaults={'name': 'Joined fast', 'requires_target': True})
        fast = TestDataFactory.create_fast()
        fast_type = ContentType.objects.get_for_model(Fast)
        # Exercise both candidate sources, both discarded histories, and a real
        # historical join that must continue to disqualify association.
        for source in ('legacy', 'period'):
            for history in ('noop', 'removed_link', 'real'):
                for i in range(10):
                    user = get_user_model().objects.create_user(
                        username=f'{source}-{history}-{i}', date_joined=self.at - timedelta(days=2))
                    profile, _ = Profile.objects.get_or_create(user=user)
                    prior_data = ({'participation_tracked': True, 'participation_changed': False}
                                  if history == 'noop' else {'participation_id': 999999}
                                  if history == 'removed_link' else {})
                    Event.objects.create(event_type=join_type, user=user, title='Joined fast',
                                         content_type=fast_type, object_id=fast.pk,
                                         timestamp=self.at - timedelta(days=1), data=prior_data)
                    self.event(EventType.NOTIFICATION_SEND, user, kind='daily_fast',
                               notif_id=str(user.pk), outcome='provider_accepted')
                    self.event(EventType.NOTIFICATION_OPENED, user, notif_id=str(user.pk))
                    if source == 'legacy':
                        Event.objects.create(event_type=join_type, user=user, title='Joined fast',
                                             content_type=fast_type, object_id=fast.pk,
                                             timestamp=self.at + timedelta(minutes=1), data={})
                    else:
                        # Use a different pair so the period cannot supersede
                        # the real historical legacy evidence in this control.
                        FastParticipation.objects.create(profile=profile, fast=None,
                                                         fast_original_id=fast.pk + 100000,
                                                         joined_at=self.at + timedelta(minutes=1))
        metric = self.report()['groups']['daily_fast']['associated_fast_join_users']
        self.assertEqual(metric['numerator'], 40)
        self.assertEqual(metric['mature_denominator'], 60)

    def test_destination_denominators_follow_hidden_kind_total(self):
        for i in range(40):
            user = self.person(f"sender-{i}")
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=str(i), kind='daily_fast')
            self.event(EventType.NOTIFICATION_DESTINATION, user, notif_id=str(i),
                       destination='fast', result='opened' if i < 20 else 'unresolved')
        for i in range(5):
            user = self.person(f"extra-{i}")
            self.event(EventType.NOTIFICATION_DESTINATION, user, notif_id=f'unknown-{i}',
                       destination='fast', result='opened')
        groups = self.report()['groups']
        self.assertEqual(groups['daily_fast']['destination:fast:unresolved']['mature_denominator'], 40)
        self.assertEqual(groups['overall']['destination:fast:unresolved']['mature_denominator'], 'suppressed')

    def test_randomized_finished_report_disclosure_relations(self):
        # Exercise the actual report builder and its final graph, not a second
        # implementation of the relation catalogue in the test.
        rng = random.Random(460)
        graphs = []
        released_event_cells = 0
        people = [self.person(f'oracle-{i}') for i in range(30)]
        profiles = [Profile.objects.get_or_create(user=u)[0] for u in people]
        prayer_type, _ = EventType.objects.get_or_create(
            code=EventType.PRAYER_SET_VIEWED, defaults={'name': 'Prayer', 'requires_target': True})
        prayer = PrayerSet.objects.create(title='Oracle prayers', church=TestDataFactory.create_church())
        prayer_ct = ContentType.objects.get_for_model(PrayerSet)

        def capture(*args):
            graph = _protect_output(*args)
            graphs.append(graph)
            return graph

        with patch('events.notification_funnel._protect_output', side_effect=capture):
            for case in range(200):
                Event.objects.filter(event_type__in=self.types.values()).delete()
                rows = []
                Event.objects.filter(event_type=prayer_type, user__in=people).delete()
                FastParticipation.objects.filter(profile__in=profiles).delete()

                def emit(code, user, delay=0, **data):
                    rows.append(Event(event_type=self.types[code],
                                      title=EventType.NOTIFICATION_LIFECYCLE_TITLES[code],
                                      user=user, timestamp=self.at + timedelta(seconds=delay), data=data))

                # Alternate fully populated partitions with boundary mixtures.
                def draw():
                    return rng.choice((0, 10, 20, 30)) if case % 2 == 0 else rng.randrange(31)

                for kind in ('daily_fast', 'admin_manual', 'prayer_thanks', 'unknown'):
                    for outcome in ('provider_accepted', 'provider_rejected', 'send_error'):
                        for i in range(draw()):
                            user = people[i]
                            nid = f'{case}-{kind}-{outcome}-{i}'
                            emit(EventType.NOTIFICATION_SEND, user, kind=kind, notif_id=nid,
                                 ticket_id=nid, outcome=outcome,
                                 error_code=rng.choice(('DeviceNotRegistered', 'MessageTooBig'))
                                 if case % 2 else 'DeviceNotRegistered')
                            if outcome == 'provider_accepted' and (case % 2 == 0 or rng.choice((True, False))):
                                emit(EventType.NOTIFICATION_RECEIPT, None, 1, notif_id=nid, ticket_id=nid,
                                     receipt_status='error' if i < 10 else 'ok', error_code='DeviceNotRegistered')
                            emit(EventType.NOTIFICATION_OPENED, user, 2, notif_id=nid,
                                 entry='tap' if i < 10 else 'cold_start',
                                 session_id=None if i < 10 else 'session')
                            for result in ('opened', 'unresolved', 'rejected'):
                                emit(EventType.NOTIFICATION_DESTINATION, user, 3, notif_id=nid,
                                     destination='fast' if i < 10 else 'prayer', result=result)
                    for i in range(draw()):
                        emit(EventType.NOTIFICATION_OPENED, people[i], 2, notif_id='missing', entry='tap')
                for trigger in ('settings', 'first_prayer', 'first_fast_join', 'other'):
                    for i in range(draw()):
                        user = people[i]
                        emit(EventType.NOTIFICATION_PROMPT, user, trigger=trigger, stage='shown')
                        emit(EventType.NOTIFICATION_PROMPT, user, 1, trigger=trigger, stage='selected',
                             choice='enable' if i < 20 else 'not_now')
                        emit(EventType.NOTIFICATION_PROMPT, user, 2, trigger=trigger, stage='os_result',
                             choice='granted' if i < 10 else 'denied')
                Event.objects.bulk_create(rows)
                for i in range(draw()):
                    Event.objects.create(event_type=prayer_type, user=people[i], title='Prayer set viewed',
                                         content_type=prayer_ct, object_id=prayer.pk,
                                         timestamp=self.at + timedelta(minutes=1),
                                         data={'session_id': 'session' if i % 2 else 'wrong'})
                    FastParticipation.objects.create(profile=profiles[i], fast=None,
                                                     fast_original_id=900000 + i,
                                                     joined_at=self.at + timedelta(minutes=1))
                report = self.report()
                released_event_cells += sum(
                    isinstance(cell.get('count', cell.get('numerator')), int)
                    and cell.get('count', cell.get('numerator')) >= 10
                    for cells in report['groups'].values() for cell in cells.values())
                self.assert_public_oracle(report)
                graph = graphs.pop()
                with self.subTest(case=case):
                    for node, value in graph.values.items():
                        if 0 < value < 10:
                            self.assertIn(node, graph.hidden)
                    for node, contributors in graph.contributors.items():
                        if graph.values[node] > 0 and len({uid for uid, _ in contributors}) < 10:
                            self.assertIn(node, graph.hidden)
                    for total, parts in graph.relations:
                        nodes = (total, *parts)
                        hidden = [n for n in nodes if n in graph.hidden]
                        if (total, parts) in graph.kind_partitions:
                            if any(n in graph.hidden for n in parts):
                                self.assertIn(total, graph.hidden)
                            continue
                        self.assertTrue(not hidden or len(hidden) == len(nodes))
                        # Brute force every subtraction this equation allows.
                        for missing in nodes:
                            others = [n for n in nodes if n != missing]
                            if all(n not in graph.hidden for n in others):
                                inferred = (sum(graph.values[n] for n in parts) if missing == total
                                            else graph.values[total] - sum(
                                                graph.values[n] for n in parts if n != missing))
                                self.assertEqual(inferred, graph.values[missing])
                                self.assertFalse(0 < inferred < 10)
                                self.assertNotIn(missing, graph.hidden)
                    for cell, field, node in graph.bindings:
                        self.assertEqual(cell[field], 'suppressed' if node in graph.hidden else graph.values[node])
                    for metrics in [*report['groups'].values(), report['permission_prompts']]:
                        for cell in metrics.values():
                            if cell.get('percent') is not None:
                                self.assertIsInstance(cell['numerator'], int)
                                self.assertIsInstance(cell['mature_denominator'], int)
                                self.assertGreaterEqual(cell['numerator'], 10)
                                self.assertGreaterEqual(cell['mature_denominator'], 10)
                                self.assertGreaterEqual(cell['mature_denominator'] - cell['numerator'], 10)
        self.assertGreater(released_event_cells, 0)

    def assert_public_oracle(self, report):
        """Independent public identities; eliminate equations using released JSON only."""
        equations = []
        known = {}

        def equation(total, parts):
            row = Counter({total: Fraction(1)})
            for part in parts:
                row[part] -= 1
            equations.append((dict(row), Fraction(0)))

        def observe(node, value):
            if isinstance(value, int):
                if node in known:
                    self.assertEqual(known[node], value)
                known[node] = value
                self.assertFalse(0 < value < 10)

        for group, cells in report['groups'].items():
            for key, cell in cells.items():
                node = (group, key)
                observe(node, cell.get('count', cell.get('numerator')))
                if 'mature_denominator' in cell:
                    if key.startswith(('provider_', 'send_error', 'accepted_tickets')):
                        parent = 'attempted_tokens'
                    elif key == 'receipts_received':
                        parent = 'accepted_tickets'
                    elif key.startswith('receipt'):
                        parent = 'receipts_received'
                    elif key.startswith('destination:'):
                        parent = 'destination_events'
                    else:
                        parent = 'open_users'
                    observe((group, parent), cell['mature_denominator'])
                    equation((group, parent), [node, (group, key, 'complement')])
                if cell.get('percent') is not None:
                    n, d = cell['numerator'], cell['mature_denominator']
                    self.assertGreaterEqual(min(n, d, d - n), 10)
            identities = [
                ('attempted_tokens', ['provider_accepted', 'provider_rejected', 'send_error']),
                ('receipts_received', ['receipt:ok', 'receipt:error']),
                ('send_failures', ['provider_rejected', 'send_error']),
                ('send_failures', [k for k in cells if k.startswith('send_error:')]),
                ('receipt:error', [k for k in cells if k.startswith('receipt_error:')]),
                ('all_opens', ['matched_opens', 'unmatched_opens']),
                ('all_opens', [k for k in cells if k.startswith('open:')]),
                ('destination_events', [k for k in cells if k.startswith('destination:')]),
                ('accepted_tickets', ['receipts_received', 'pending_receipts']),
                ('provider_accepted', ['accepted_tickets', 'ticketless']),
                ('matched_opens', ['opens_missing_session', 'session_present']),
            ]
            for total, parts in identities:
                equation((group, total), [(group, p) for p in parts])
        for key in ('open_users', 'associated_prayer_users', 'associated_fast_join_users'):
            for group in report['groups']:
                if group != 'overall':
                    equation(('overall', key), [(group, key), (group, key, 'outside')])
        for key in report['groups']['overall']:
            if not key.startswith('associated_'):
                equation(('overall', key), [(g, key) for g in report['groups'] if g != 'overall'])
        for key, cell in report['permission_prompts'].items():
            observe(('prompt', key), cell.get('count', cell.get('numerator')))
            if 'mature_denominator' in cell:
                trigger, stage, _ = key.split(':')
                parent = trigger + (':selected:enable' if stage == 'os_result' else ':shown:None')
                observe(('prompt', parent), cell['mature_denominator'])
                equation(('prompt', parent), [('prompt', key), ('prompt', key, 'complement')])
                if cell.get('percent') is not None:
                    self.assertGreaterEqual(min(cell['numerator'], cell['mature_denominator'] - cell['numerator']), 10)
        # Sparse rational Gaussian elimination combines intersecting identities,
        # including ratios' exact numerator/denominator aliases, to a fixed basis.
        basis = {}
        for row, rhs in [*equations, *(({n: Fraction(1)}, Fraction(v)) for n, v in known.items())]:
            row = {n: Fraction(v) for n, v in row.items() if v}
            while row:
                pivot = min(row, key=repr)
                factor = row[pivot]
                if pivot not in basis:
                    basis[pivot] = ({n: v / factor for n, v in row.items()}, rhs / factor)
                    break
                other, value = basis[pivot]
                rhs -= factor * value
                for n, v in other.items():
                    row[n] = row.get(n, 0) - factor * v
                    if not row[n]:
                        del row[n]
            else:
                self.assertEqual(rhs, 0)
        resolved = {}
        for pivot in sorted(basis, key=repr, reverse=True):
            row, rhs = basis[pivot]
            if all(n == pivot or n in resolved for n in row):
                value = rhs - sum(v * resolved[n] for n, v in row.items() if n != pivot)
                resolved[pivot] = value
                self.assertFalse(0 < value < 10, (pivot, value))

    def test_one_user_cannot_release_repeated_errors(self):
        for i in range(12):
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=str(i), kind='admin_manual',
                       outcome='send_error', error_code='DeviceNotRegistered')
        groups = self.report()['groups']
        self.assertNotIn('admin_manual', groups)
        self.assertEqual(groups['other_kinds']['send_error:DeviceNotRegistered']['numerator'], 'suppressed')
        self.assertEqual(groups['overall']['attempted_tokens']['count'], 'suppressed')

    def test_excluded_ticket_receipt_does_not_advance_freshness(self):
        self.user.is_staff = True
        self.user.save()
        self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast', notif_id='excluded',
                   ticket_id='ticket', outcome='provider_accepted')
        before = self.report()
        self.event(EventType.NOTIFICATION_RECEIPT, notif_id='excluded', ticket_id='ticket',
                   receipt_status='ok', at=self.at + timedelta(days=1))
        self.assertEqual(self.report(), before)

    def test_os_result_unmatched_and_text(self):
        self.event(EventType.NOTIFICATION_PROMPT, self.user, trigger='settings', stage='os_result', choice='denied')
        cells = self.report()['permission_prompts']
        self.assertEqual(cells['settings:unmatched']['count'], 'suppressed')
        self.assertEqual(cells['settings:os_result:denied']['numerator'], 0)
        out = StringIO()
        call_command('notification_funnel', as_of='2026-03-12', format='text', stdout=out)
        self.assertIn('Minimum cell: 10', out.getvalue())
        self.assertIn('  attempted_tokens:', out.getvalue())
        self.assertNotIn('"groups":', out.getvalue())

    def test_lifecycle_row_limit(self):
        self.assertEqual(notification_funnel.MAX_LIFECYCLE_ROWS, 200_000)

    def test_row_guard_inclusive_boundary(self):
        # Small patched limit exercises the same > comparison without 200k rows.
        with patch('events.notification_funnel.MAX_LIFECYCLE_ROWS', 2):
            for _ in range(2):
                self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast')
            self.report()
            self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast')
            with self.assertRaisesMessage(ValueError, 'exceeds 2 lifecycle rows'):
                self.report()

    def test_distinct_user_receipt_and_residual_floors(self):
        # Counts are constructed here, independently of production contributor sets.
        # Each sparse population has ten events but only one distinct user.
        for sparse in ('receipts', 'pending', 'ticketless', 'category'):
            with self.subTest(sparse=sparse):
                Event.objects.filter(event_type__in=self.types.values()).delete()
                for i in range(50):
                    repeated = i < 10
                    user = self.user if repeated else self.person(f'floor-{i}')
                    outcome = 'provider_accepted' if i < 30 else 'provider_rejected'
                    if sparse == 'category' and repeated:
                        # Deliberate legacy data: attempts still count, but no
                        # recognized outcome does. Its remainder has one user.
                        outcome = 'legacy_unknown'
                    data = dict(notif_id=str(i), kind='daily_fast', outcome=outcome)
                    if not (sparse == 'ticketless' and repeated):
                        data['ticket_id'] = str(i)
                    self.event(EventType.NOTIFICATION_SEND, user, **data)
                    receipt = (repeated if sparse == 'receipts' else 10 <= i < 30)
                    if receipt:
                        self.event(EventType.NOTIFICATION_RECEIPT,
                                   at=self.at + timedelta(seconds=1), notif_id=str(i),
                                   ticket_id=str(i), receipt_status='ok')
                report = self.report()
                cells = report['groups']['daily_fast']
                self.assertEqual(cells['attempted_tokens']['count'], 'suppressed')
                self.assertEqual(cells['provider_accepted']['numerator'], 'suppressed')
                if sparse in ('receipts', 'pending'):
                    self.assertEqual(cells['receipts_received']['numerator'], 'suppressed')
                    self.assertEqual(cells['receipt:ok']['mature_denominator'], 'suppressed')
                self.assertEqual(report['groups']['overall']['attempted_tokens']['count'], 'suppressed')
                self.assert_public_oracle(report)

    def test_repeated_unmatched_open_and_destination_user_floor(self):
        for i in range(10):
            self.event(EventType.NOTIFICATION_OPENED, self.user,
                       notif_id=f'missing-{i}', entry='tap')
            self.event(EventType.NOTIFICATION_DESTINATION, self.user,
                       notif_id=f'missing-{i}', destination='fast', result='opened')
        report = self.report()
        for group in ('unclassified', 'overall'):
            cells = report['groups'][group]
            for key in ('unmatched_opens', 'matched_opens', 'open:tap'):
                self.assertEqual(cells[key]['count'], 'suppressed')
            self.assertEqual(cells['destination:fast:opened']['numerator'], 'suppressed')
            self.assertEqual(cells['destination:fast:unresolved']['mature_denominator'], 'suppressed')
        self.assert_public_oracle(report)

    def test_correlation_edge_cases_and_category_remainders(self):
        graphs = []

        def capture(*args):
            graph = _protect_output(*args)
            graphs.append(graph)
            return graph

        # Duplicate composite ticket keys retain the last chronological send;
        # the same ticket id under another notification id is independent.
        for delay, nid in ((0, 'duplicate'), (2, 'duplicate'), (0, 'separate')):
            self.event(EventType.NOTIFICATION_SEND, self.user,
                       at=self.at + timedelta(seconds=delay), kind='daily_fast',
                       notif_id=nid, ticket_id='shared', outcome='provider_accepted')
        for delay in (-1, 1, 3, 4):
            self.event(EventType.NOTIFICATION_RECEIPT,
                       at=self.at + timedelta(seconds=delay), notif_id='duplicate',
                       ticket_id='shared', receipt_status='ok')
        self.event(EventType.NOTIFICATION_RECEIPT, notif_id='separate',
                   ticket_id='shared', receipt_status='ok')
        self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast',
                   notif_id='ticketless', outcome='provider_accepted')
        # Deliberate legacy categories exercise the internal remainders. Neither
        # drops the row: unknown outcomes still count as attempts, and unknown
        # entries still count as opens. Open correlation needs no ticket_id.
        self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast',
                   outcome='legacy_unknown')
        self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id='ticketless', entry='legacy_unknown')
        old = self.at - timedelta(days=40)
        self.event(EventType.NOTIFICATION_SEND, self.user, at=old, kind='daily_fast',
                   notif_id='old', ticket_id='old', outcome='provider_accepted')
        self.event(EventType.NOTIFICATION_RECEIPT, notif_id='old', ticket_id='old', receipt_status='ok')
        self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id='old', entry='tap')
        self.event(EventType.NOTIFICATION_DESTINATION, self.user, notif_id='old',
                   destination='fast', result='opened')
        with patch('events.notification_funnel._protect_output', side_effect=capture):
            report = self.report()
        values = graphs[0].values
        # Fixture-derived expectations inspect counts, never contributor metadata.
        # Five in-window sends fold into other_kinds (one contributor). Both
        # duplicate sends count as tickets, but only the receipt at +3s joins
        # the last send at +2s; +4s is deduplicated. The separate notif_id joins
        # independently. The pre-window send supplies no correlation state.
        for key, expected in (('attempted_tokens', 5), ('provider_accepted', 4),
                              ('accepted_tickets', 3), ('receipts_received', 2),
                              ('matched_opens', 1), ('accepted_without_ticket', 1)):
            self.assertEqual(values[('other_kinds', key)], expected)
        self.assertEqual(values[('other_kinds', 'remainder', 0)], 1)
        # Every open is matched or unmatched, even with an unknown entry.
        self.assertEqual(values[('other_kinds', 'remainder', 5)], 0)
        self.assertEqual(values[('other_kinds', 'remainder', 6)], 1)
        self.assertEqual(values[('unclassified', 'unmatched_opens')], 1)
        self.assertEqual(values[('unclassified', 'destination_events')], 1)
        self.assert_public_oracle(report)

    def test_row_guard_command_error(self):
        with patch('events.notification_funnel.MAX_LIFECYCLE_ROWS', 0):
            self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast')
            with self.assertRaisesMessage(CommandError, 'reduce --since-days'):
                call_command('notification_funnel', as_of='2026-03-12')

    def test_postgres_read_only_snapshot_setup(self):
        conn = MagicMock(in_atomic_block=False, vendor='postgresql')
        with patch('events.notification_funnel.connection', conn), \
                patch('events.notification_funnel.transaction.atomic', return_value=nullcontext()), \
                patch('events.notification_funnel._build_report', return_value={}) as build:
            build_report(date(2026, 3, 12))
        conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        build.assert_called_once()

    def test_send_and_receipt_error_breakdowns(self):
        for i in range(60):
            user = self.person(f'errors-{i}')
            outcome = 'provider_accepted' if i < 40 else 'send_error'
            self.event(EventType.NOTIFICATION_SEND, user, kind='daily_fast', notif_id=str(i),
                       ticket_id=str(i), outcome=outcome,
                       error_code='DeviceNotRegistered' if i < 50 else 'MessageTooBig')
            if i < 30:
                self.event(EventType.NOTIFICATION_RECEIPT, notif_id=str(i), ticket_id=str(i),
                           receipt_status='ok' if i < 10 else 'error',
                           error_code='DeviceNotRegistered' if i < 20 else 'MessageTooBig')
        report = self.report()
        cells = report['groups']['daily_fast']
        self.assertEqual(cells['send_error:DeviceNotRegistered']['numerator'], 10)
        self.assertEqual(cells['send_error:MessageTooBig']['numerator'], 10)
        self.assertEqual(cells['receipt_error:DeviceNotRegistered']['numerator'], 10)
        self.assertEqual(cells['receipt_error:MessageTooBig']['numerator'], 10)
        self.assert_public_oracle(report)

    def test_pooled_kinds_can_clear_both_floors(self):
        for kind in ('admin_manual', 'test'):
            for i in range(6):
                self.event(EventType.NOTIFICATION_SEND, self.person(f'{kind}-{i}'), kind=kind,
                           outcome='provider_accepted')
        groups = self.report()['groups']
        self.assertNotIn('admin_manual', groups)
        self.assertNotIn('test', groups)
        self.assertEqual(groups['other_kinds']['attempted_tokens']['count'], 12)
        self.assertEqual(groups['overall']['attempted_tokens']['count'], 12)

    def test_public_oracle_detects_derived_small_receipt_complement(self):
        # Both released values pass the raw threshold, but subtraction gives 5.
        report = {'groups': {'overall': {
            'accepted_tickets': {'numerator': 20, 'mature_denominator': 40, 'percent': 50},
            'receipts_received': {'numerator': 15, 'mature_denominator': 20, 'percent': None},
        }}, 'permission_prompts': {}}
        report['groups']['daily_fast'] = dict(report['groups']['overall'])
        with self.assertRaises(AssertionError):
            self.assert_public_oracle(report)
