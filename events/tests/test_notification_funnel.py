"""Synthetic report contracts; no telemetry flag or producer is required."""
import json
import random
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase, override_settings

from events.growth_cohorts import build_report as cohort_report
from events.models import Event, EventType
from events.notification_funnel import _protect_output, _release, build_report
from hub.models import Fast, FastParticipation, Profile
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
                                    timestamp=at or self.at, data=data)

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
        other = get_user_model().objects.create_user(username='other', date_joined=self.at)
        for i in range(20):
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=str(i), ticket_id=str(i),
                       kind='daily_fast', outcome='provider_accepted')
            self.event(EventType.NOTIFICATION_OPENED, self.user if i < 10 else other,
                       at=self.at + timedelta(minutes=1), notif_id=str(i), entry='tap')
            if i < 10:
                self.event(EventType.NOTIFICATION_RECEIPT, at=self.at + timedelta(minutes=2),
                           notif_id=str(i), ticket_id=str(i), receipt_status='ok')
        # Keep the accepted-ticket numerator and its complement above k.
        for _ in range(20):
            self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast',
                       outcome='provider_rejected')
        report = self.report()['groups']
        self.assertEqual(report['daily_fast']['matched_opens']['count'], 10)
        self.assertEqual(report['unclassified']['unmatched_opens']['count'], 10)
        self.assertEqual(report['daily_fast']['receipts_received']['percent'], 50)
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True)
        self.assertEqual(self.report()['groups']['daily_fast']['attempted_tokens']['count'], 0)

    def test_freshness_excludes_notification_rows_in_cohorts(self):
        before = cohort_report(date(2026, 3, 12))['data_freshness']
        self.event(EventType.NOTIFICATION_SEND, self.user, kind='daily_fast')
        self.assertEqual(cohort_report(date(2026, 3, 12))['data_freshness'], before)
        self.assertEqual(self.report()['data_freshness'], self.at.isoformat())

    def test_prompt_cells_are_suppressed(self):
        self.event(EventType.NOTIFICATION_PROMPT, self.user, stage='selected', choice='enable', trigger='settings')
        cells = self.report()['permission_prompts']
        self.assertEqual(cells['settings:selected:enable']['numerator'], 'suppressed')

    def test_attribution_boundaries_and_sessions(self):
        prayer_type, _ = EventType.objects.get_or_create(
            code=EventType.PRAYER_SET_VIEWED, defaults={'name': 'Prayer'})
        # Ten at each boundary; ten too late, ten with another session.
        for i in range(40):
            user = get_user_model().objects.create_user(username=f'boundary{i}', date_joined=self.at)
            self.event(EventType.NOTIFICATION_SEND, user, notif_id=str(i), kind='daily_fast',
                       outcome='provider_accepted')
            self.event(EventType.NOTIFICATION_OPENED, user, notif_id=str(i), session_id='session')
            delay = (0 if i < 10 else 30 if i < 20 else 31 if i < 30 else 1)
            Event.objects.create(event_type=prayer_type, user=user,
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
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=str(i), ticket_id=str(i),
                       kind='daily_fast', outcome='provider_accepted')
            self.event(EventType.NOTIFICATION_RECEIPT, notif_id=str(i), ticket_id='wrong', receipt_status='ok')
        self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id='0',
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
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=str(i), ticket_id=str(i),
                       kind='daily_fast', outcome='provider_accepted' if i < 20 else 'provider_rejected')
            if i < 10:
                self.event(EventType.NOTIFICATION_RECEIPT, notif_id=str(i), ticket_id=str(i),
                           receipt_status='ok')
        for _ in range(5):
            self.event(EventType.NOTIFICATION_SEND, self.user, kind='unknown', outcome='provider_rejected')
        groups = self.report()['groups']
        for group in ('daily_fast', 'overall'):
            self.assertEqual(groups[group]['attempted_tokens']['count'], 'suppressed')
            for key in ('provider_accepted', 'provider_rejected', 'accepted_tickets', 'receipts_received',
                        'receipt:ok'):
                with self.subTest(group=group, key=key):
                    self.assertEqual(groups[group][key]['numerator'], 'suppressed')
                    self.assertEqual(groups[group][key]['mature_denominator'], 'suppressed')
                    self.assertIsNone(groups[group][key]['percent'])
                    self.assertIsNone(groups[group][key]['wilson_95'])

    def test_entry_breakdown_includes_unmatched_opens(self):
        other = get_user_model().objects.create_user(username='entry-other', date_joined=self.at)
        for i in range(10):
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=f'matched-{i}', kind='daily_fast')
            self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id=f'matched-{i}', entry='tap')
            # Wrong-user correlation and sends preceding the report window both
            # remain raw opens with a known entry and an unknown kind.
            self.event(EventType.NOTIFICATION_OPENED, other, notif_id=f'matched-{i}', entry='tap')
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=f'old-{i}', kind='daily_fast',
                       at=self.at - timedelta(days=40))
            self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id=f'old-{i}', entry='cold_start')
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
                nid = f'{kind}-{i}'
                self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=nid, kind=kind)
                self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id=nid, entry='tap')
        groups = self.report()['groups']
        for group in ('overall', 'daily_fast', 'prayer_request_notifications'):
            with self.subTest(group=group):
                self.assertEqual(groups[group]['opens_missing_session']['count'], 'suppressed')
        # A fully publishable partition still exposes the correct raw counts.
        for i in range(5):
            nid = f'extra-{i}'
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=nid, kind='prayer_thanks')
            self.event(EventType.NOTIFICATION_OPENED, self.user, notif_id=nid, entry='tap')
        groups = self.report()['groups']
        self.assertEqual(groups['overall']['opens_missing_session']['count'], 25)
        self.assertEqual(groups['daily_fast']['opens_missing_session']['count'], 15)
        self.assertEqual(groups['prayer_request_notifications']['opens_missing_session']['count'], 10)

    def test_first_fast_join_uses_only_eligible_prior_evidence(self):
        join_type, _ = EventType.objects.get_or_create(
            code=EventType.USER_JOINED_FAST, defaults={'name': 'Joined fast'})
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
                    Event.objects.create(event_type=join_type, user=user,
                                         content_type=fast_type, object_id=fast.pk,
                                         timestamp=self.at - timedelta(days=1), data=prior_data)
                    self.event(EventType.NOTIFICATION_SEND, user, kind='daily_fast',
                               notif_id=str(user.pk), outcome='provider_accepted')
                    self.event(EventType.NOTIFICATION_OPENED, user, notif_id=str(user.pk))
                    if source == 'legacy':
                        Event.objects.create(event_type=join_type, user=user,
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
            self.event(EventType.NOTIFICATION_SEND, self.user, notif_id=str(i), kind='daily_fast')
            self.event(EventType.NOTIFICATION_DESTINATION, self.user, notif_id=str(i),
                       destination='fast', result='opened' if i < 20 else 'unresolved')
        for i in range(5):
            self.event(EventType.NOTIFICATION_DESTINATION, self.user, notif_id=f'unknown-{i}',
                       destination='fast', result='opened')
        groups = self.report()['groups']
        for group in ('daily_fast', 'overall'):
            self.assertEqual(groups[group]['destination:fast:unresolved']['mature_denominator'], 'suppressed')

    def test_randomized_finished_report_disclosure_relations(self):
        # Exercise the actual report builder and its final graph, not a second
        # implementation of the relation catalogue in the test.
        rng = random.Random(460)
        graphs = []

        def capture(*args):
            graph = _protect_output(*args)
            graphs.append(graph)
            return graph

        with patch('events.notification_funnel._protect_output', side_effect=capture):
            for case in range(200):
                Event.objects.filter(event_type__in=self.types.values()).delete()
                rows = []
                for kind in ('daily_fast', 'unknown'):
                    nid = f'{case}-{kind}'
                    for outcome in ('provider_accepted', 'provider_rejected', 'send_error'):
                        for _ in range(rng.randrange(31)):
                            rows.append(Event(event_type=self.types[EventType.NOTIFICATION_SEND],
                                              user=self.user, timestamp=self.at,
                                              data={'kind': kind, 'notif_id': nid, 'outcome': outcome}))
                    for result in ('opened', 'unresolved', 'rejected'):
                        for _ in range(rng.randrange(31)):
                            rows.append(Event(event_type=self.types[EventType.NOTIFICATION_DESTINATION],
                                              user=self.user, timestamp=self.at + timedelta(seconds=1),
                                              data={'notif_id': nid, 'destination': 'fast', 'result': result}))
                Event.objects.bulk_create(rows)
                report = self.report()
                graph = graphs.pop()
                with self.subTest(case=case):
                    for node, value in graph.values.items():
                        if 0 < value < 10:
                            self.assertIn(node, graph.hidden)
                    for total, parts in graph.relations:
                        nodes = (total, *parts)
                        hidden = [n for n in nodes if n in graph.hidden]
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
