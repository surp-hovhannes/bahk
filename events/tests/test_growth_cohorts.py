import json
import os
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from events.growth_cohorts import (
    ACTIVATION, RETENTION, _configured, _metric, _record_action, _suppress_distribution, _suppress_nested, build_report,
)
from events.models import Event, EventType
from hub.models import FastParticipation, Profile
from tests.base import BaseTestCase


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class GrowthCohortTests(BaseTestCase):
    def setUp(self):
        self.target = self.create_fast()
        self.joined = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
        self.user = self.make_user('cohort', self.joined)
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        Profile.objects.filter(pk=self.profile.pk).update(timezone='America/Los_Angeles')

    def make_user(self, name, joined, **kwargs):
        user = get_user_model().objects.create_user(username=name, **kwargs)
        get_user_model().objects.filter(pk=user.pk).update(date_joined=joined)
        return user

    def event(self, code, at, **kwargs):
        if EventType._requires_target_for_code(code):
            kwargs.setdefault('target', self.target)
        event = Event.create_event(code, user=self.user, title='Synthetic cohort action', **kwargs)
        Event.objects.filter(pk=event.pk).update(timestamp=at)
        return event

    def report(self, at=date(2026, 5, 1), minimum=1):
        return build_report(at, minimum)

    def metric(self, report, name):
        return report['cohorts'][0]['metrics'][name]

    def synthetic_group(self, prefix, joined, size):
        users = get_user_model().objects.bulk_create([
            get_user_model()(username=f'{prefix}{index}', date_joined=joined)
            for index in range(size)
        ])
        Profile.objects.bulk_create([
            Profile(user=user, timezone='America/Los_Angeles') for user in users
        ])
        for user in users[:size // 2]:
            for delta in (timedelta(hours=1), timedelta(days=1),
                          timedelta(days=7), timedelta(days=30)):
                event = Event.create_event(EventType.CHECKLIST_USED, user=user,
                                           title='Synthetic cohort checklist')
                Event.objects.filter(pk=event.pk).update(timestamp=joined + delta)

    def test_fully_mature_cells_release_counts_percent_and_wilson(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True)
        self.synthetic_group('mature', self.joined, 40)
        for minimum in (1, 10):
            with self.subTest(minimum=minimum):
                cohort = self.report(minimum=minimum)['cohorts'][0]
                for metric in cohort['metrics'].values():
                    self.assertEqual(metric['numerator'], 20)
                    self.assertEqual(metric['mature_denominator'], 40)
                    self.assertEqual(metric['immature'], 0)
                    self.assertEqual(metric['percent'], 50)
                    self.assertEqual(metric['wilson_95'], [35.2, 64.8])
                # Mirroring keeps zero visible even when distribution suppression
                # hides the only nonempty category (its complement is zero).
                for metric in cohort['first_action_within_7d'].values():
                    self.assertEqual(metric['immature'], 0)
                self.assertEqual(cohort['first_action_within_7d'][EventType.CHECKLIST_USED]['numerator'],
                                 'suppressed')

    def test_signups_before_matures_all_windows_without_weakening_suppression(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True)
        as_of = date(2026, 5, 1)
        signup_cutoff = as_of - timedelta(days=31)
        self.synthetic_group('older', self.joined, 40)
        self.make_user('recent', datetime(2026, 4, 30, 23, tzinfo=timezone.utc))
        for minimum in (1, 10):
            with self.subTest(minimum=minimum):
                unrestricted = build_report(as_of, minimum, 'all')
                restricted = build_report(as_of, minimum, 'all', signup_cutoff)
                self.assertIsNone(unrestricted['signups_before'])
                self.assertEqual(restricted['signups_before'], '2026-03-31T00:00:00+00:00')
                cohort = restricted['cohorts'][0]
                self.assertEqual(cohort['population'], 40)
                for name, metric in cohort['metrics'].items():
                    self.assertEqual(metric['immature'], 0)
                    self.assertEqual(metric['numerator'], 20)
                    self.assertEqual(metric['mature_denominator'], 40)
                    self.assertEqual(metric['percent'], 50)
                    other = unrestricted['cohorts'][0]['metrics'][name]
                    if minimum == 10:
                        self.assertIsNone(other['percent'])
                        self.assertEqual(other['numerator'], 'suppressed')
                        self.assertEqual(other['mature_denominator'], 'suppressed')
                    else:
                        # At k=1 a single immature signup is not a small cell.
                        self.assertEqual(other['percent'], 50)
                for metric in cohort['first_action_within_7d'].values():
                    self.assertEqual(metric['immature'], 0)
                # Existing distribution suppression still hides a sole nonempty category.
                self.assertEqual(cohort['first_action_within_7d'][EventType.CHECKLIST_USED]['numerator'],
                                 'suppressed')

    @override_settings(ANALYTICS_EXCLUDED_EMAIL_DOMAINS=['example.invalid'])
    def test_signups_before_bounds_population_and_sequential_exclusions(self):
        cutoff = date(2026, 3, 31)
        boundary = datetime(2026, 3, 31, tzinfo=timezone.utc)
        self.event(EventType.CHECKLIST_USED, self.joined + timedelta(hours=1))
        for prefix, joined in (('old', boundary - timedelta(microseconds=1)),
                               ('equal', boundary), ('later', boundary + timedelta(days=1))):
            self.make_user(prefix + 'staff', joined, is_staff=True, is_superuser=True)
            self.make_user(prefix + 'super', joined, is_superuser=True)
            self.make_user(prefix + 'domain', joined, email=prefix + '@example.invalid')
        old_id = self.make_user('oldid', self.joined)
        new_id = self.make_user('newid', boundary)
        before = build_report(date(2026, 5, 1), 1, 'all', cutoff)
        with override_settings(ANALYTICS_EXCLUDED_USER_IDS=[old_id.pk, new_id.pk]):
            restricted = build_report(date(2026, 5, 1), 1, 'all', cutoff)
            self.assertEqual(set(restricted['exclusions_sequential'].values()), {1})
            self.assertEqual(restricted['cohorts'][0]['population'], 1)
            # Later signup actions cannot affect any cohort count or diagnostic.
            self.synthetic_group('late-actions', boundary, 20)
            self.assertEqual(build_report(date(2026, 5, 1), 1, 'all', cutoff)['cohorts'],
                             restricted['cohorts'])
        self.assertEqual(before['cohorts'][0]['population'], 2)
        self.assertEqual(build_report(date(2026, 5, 1), 1),
                         build_report(date(2026, 5, 1), 1, signups_before=None))

    def test_signups_before_command_and_validation(self):
        output = StringIO()
        call_command('growth_cohorts', '--as-of', '2026-05-01', stdout=output)
        self.assertIsNone(json.loads(output.getvalue())['signups_before'])
        with self.assertRaisesMessage(CommandError, 'invalid fromisoformat value'):
            call_command('growth_cohorts', '--as-of', '2026-05-01', '--signups-before', 'invalid')
        for cutoff in ('2026-03-31', '2026-05-01'):
            output = StringIO()
            call_command('growth_cohorts', '--as-of', '2026-05-01',
                         '--signups-before', cutoff, stdout=output)
            self.assertEqual(json.loads(output.getvalue())['signups_before'], cutoff + 'T00:00:00+00:00')
        self.assertEqual(build_report(date(2026, 5, 1), 1)['cohorts'],
                         build_report(date(2026, 5, 1), 1, signups_before=date(2026, 5, 1))['cohorts'])
        with self.assertRaisesMessage(ValueError, 'signups_before must be <= as_of'):
            build_report(date(2026, 5, 1), signups_before=date(2026, 5, 2))
        with self.assertRaisesMessage(CommandError, 'signups_before must be <= as_of'):
            call_command('growth_cohorts', '--as-of', '2026-05-01', '--signups-before', '2026-05-02')

    def test_granularity_aggregates_same_unsuppressed_metrics(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True)
        for index, day in enumerate((date(2026, 1, 12), date(2026, 1, 19),
                                     date(2026, 2, 9), date(2026, 2, 16))):
            self.synthetic_group(f'group{index}-', datetime.combine(day, datetime.min.time(),
                                                                  tzinfo=timezone.utc), 4)
        reports = {g: build_report(date(2026, 5, 1), 1, g) for g in ('week', 'month', 'all')}
        self.assertEqual([c['month_start'] for c in reports['month']['cohorts']],
                         ['2026-01-01', '2026-02-01'])
        self.assertEqual(reports['all']['cohorts'][0]['cohort'], 'all_signups')
        for granularity, report in reports.items():
            self.assertEqual(report['granularity'], granularity)
            self.assertEqual(report['exclusions_sequential'], reports['week']['exclusions_sequential'])
            self.assertEqual(sum(c['population'] for c in report['cohorts']), 16)
            for name in (*ACTIVATION, *RETENTION):
                for field, expected in (('numerator', 8), ('mature_denominator', 16)):
                    total = sum(c['metrics'][name][field] for c in report['cohorts'])
                    weekly = sum(c['metrics'][name][field] for c in reports['week']['cohorts'])
                    self.assertEqual(total, weekly)
                    self.assertEqual(total, expected)
        self.assertEqual(self.report(), build_report(date(2026, 5, 1), 1, 'week'))

    def test_all_granularity_empty_population(self):
        report = build_report(date(2000, 1, 1), granularity='all')
        self.assertEqual(len(report['cohorts']), 1)
        self.assertEqual(report['cohorts'][0]['cohort'], 'all_signups')
        self.assertEqual(report['cohorts'][0]['population'], '<10')

    def test_command_granularity_choices(self):
        for granularity in ('week', 'month', 'all'):
            output = StringIO()
            call_command('growth_cohorts', '--as-of', '2026-05-01',
                         '--granularity', granularity, stdout=output)
            report = json.loads(output.getvalue())
            self.assertEqual(report['granularity'], granularity)
            key, value = {'week': ('week_start', '2026-03-02'),
                          'month': ('month_start', '2026-03-01'),
                          'all': ('cohort', 'all_signups')}[granularity]
            self.assertEqual(report['cohorts'][0][key], value)
        with self.assertRaisesMessage(CommandError, 'invalid choice'):
            call_command('growth_cohorts', '--as-of', '2026-05-01', '--granularity', 'day')
        with self.assertRaisesMessage(ValueError, 'granularity must be'):
            build_report(date(2026, 5, 1), granularity='day')

    def test_stream_user_outside_population_snapshot_is_ignored(self):
        at = self.joined + timedelta(hours=1)
        code = EventType.DEVOTIONAL_VIEWED
        self.event(code, at)
        outsider = self.make_user('lateeligible', self.joined, is_staff=True)
        before = self.report()
        # Only the action stream includes this user, simulating eligibility
        # changing after the initial population query without a timing race.
        from events.participation_analytics import analytics_events
        source = mock.Mock()
        with mock.patch('events.growth_cohorts.analytics_events',
                        side_effect=[source, analytics_events()]) as events:
            stream = source.filter.return_value.filter.return_value.order_by.return_value.values_list.return_value
            stream.iterator.return_value = iter([
                (self.user.pk, at, code),
                (outsider.pk, at, code),
            ])
            self.assertEqual(self.report(), before)
        stream.iterator.assert_called_once()
        self.assertEqual(events.call_count, 2)

    def test_report_inside_outer_atomic_matches_report_outside(self):
        self.event(EventType.DEVOTIONAL_VIEWED, self.joined + timedelta(hours=1))
        before = self.report()
        # BaseTestCase already supplies an atomic block; add an explicit caller.
        with transaction.atomic():
            self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
            self.assertEqual(self.report(), before)

    def test_population_snapshot_survives_staff_flip(self):
        # This proves the Python population snapshot, not PostgreSQL isolation.
        from events.participation_analytics import analytics_events
        self.event(EventType.DEVOTIONAL_VIEWED, self.joined + timedelta(hours=1))
        before = self.report()

        def flip_staff():
            get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True)
            return analytics_events()

        with mock.patch('events.growth_cohorts.analytics_events', side_effect=flip_staff):
            self.assertEqual(self.report(), before)
        self.assertTrue(get_user_model().objects.get(pk=self.user.pk).is_staff)

    def test_sql_window_does_not_fetch_late_events(self):
        late = self.joined + timedelta(days=40)
        self.event(EventType.DEVOTIONAL_VIEWED, late)
        self.event(EventType.USER_JOINED_FAST, late)
        FastParticipation.objects.create(profile=self.profile, fast=self.create_fast(), joined_at=late)
        with mock.patch('events.growth_cohorts._record_action', wraps=_record_action) as record:
            report = self.report()
        record.assert_not_called()
        self.assertEqual(self.metric(report, 'd30')['numerator'], '<1')

    def test_sql_fall_dst_safety_bound_keeps_31d23h_candidate(self):
        joined = datetime(2026, 10, 31, 7, tzinfo=timezone.utc)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        at = joined + timedelta(days=31, hours=23)
        self.event(EventType.DEVOTIONAL_VIEWED, at)
        with mock.patch('events.growth_cohorts._record_action', wraps=_record_action) as record:
            report = self.report(date(2026, 12, 3))
        record.assert_called_once_with(mock.ANY, self.user.pk, at, EventType.DEVOTIONAL_VIEWED)
        # Fetched by the safe SQL bound, rejected by the exact local day-30 bound.
        self.assertEqual(self.metric(report, 'd30')['numerator'], '<1')

    def test_reverse_action_order_keeps_participation_tie(self):
        from zoneinfo import ZoneInfo
        at = self.joined + timedelta(hours=1)
        actions = [(at, EventType.DEVOTIONAL_VIEWED, False),
                   (at, EventType.USER_JOINED_FAST, True),
                   (at, EventType.PRAYER_SET_VIEWED, False)]
        results = []
        for ordered in (actions, list(reversed(actions))):
            snapshot = {self.user.pk: {'joined': self.joined, 'end': self.joined + timedelta(days=31),
                                      'tz': ZoneInfo('America/Los_Angeles'),
                                      'signup_day': self.joined.astimezone(ZoneInfo('America/Los_Angeles')).date(),
                                      'first': None, 'days': set()}}
            for timestamp, code, participation in ordered:
                _record_action(snapshot, self.user.pk, timestamp, code, participation=participation)
            results.append(snapshot)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][self.user.pk]['first'], (at, 0, EventType.USER_JOINED_FAST))

    def test_noops_and_legacy_screens_do_not_advance_freshness(self):
        self.event(EventType.DEVOTIONAL_VIEWED, self.joined + timedelta(hours=1))
        before = self.report()
        self.event(EventType.USER_JOINED_FAST, self.joined + timedelta(days=2),
                   data={'participation_tracked': True, 'participation_changed': False})
        self.event(EventType.SCREEN_VIEW, self.joined + timedelta(days=3))
        self.assertEqual(self.report(), before)

    def test_dst_exact_local_day_and_elapsed_activation(self):
        # March 8 is 23h in LA; March 9 00:15 is only 21h45 after signup.
        self.event(EventType.PRAYER_SET_VIEWED, datetime(2026, 3, 9, 7, 15, tzinfo=timezone.utc))
        report = self.report()
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 'suppressed')
        self.assertEqual(self.metric(report, 'd1')['numerator'], 'suppressed')
        self.assertEqual(self.metric(report, 'd7')['numerator'], '<1')
        # Freshness is published to the hour, never one row's exact timestamp.
        self.assertEqual(report['data_freshness'], '2026-03-09T07:00:00+00:00')

    def test_activation_and_closed_day_maturity_boundaries(self):
        joined = datetime(2026, 3, 8, tzinfo=timezone.utc)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        self.event(EventType.DEVOTIONAL_VIEWED, joined + timedelta(hours=24))
        # March 10 midnight minus 24h lag closes the 24h window exactly.
        report = self.report(date(2026, 3, 10))
        self.assertEqual(self.metric(report, 'activation_24h')['mature_denominator'], 1)
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 'suppressed')
        # Immature users leave no mature denominator; the immature cell is then
        # hidden too, since population minus it would recover the denominator.
        self.assertEqual(self.metric(report, 'activation_72h')['mature_denominator'], '<1')
        self.assertEqual(self.metric(report, 'activation_72h')['immature'], 'suppressed')
        self.assertEqual(self.metric(self.report(date(2026, 3, 9)), 'activation_24h')['mature_denominator'], '<1')
        # Signup is March 7 locally; March 8 has closed by March 10 UTC.
        self.assertEqual(self.metric(report, 'd1')['mature_denominator'], 1)
        self.assertEqual(self.metric(self.report(date(2026, 3, 9)), 'd1')['mature_denominator'], '<1')

    @override_settings(ANALYTICS_EXCLUDED_USER_IDS=[], ANALYTICS_EXCLUDED_EMAIL_DOMAINS=['example.invalid'])
    def test_exclusions_and_fallback(self):
        self.make_user('staff', self.joined, is_staff=True)
        self.make_user('super', self.joined, is_superuser=True)
        self.make_user('domain', self.joined, email='test@example.invalid')
        other = self.make_user('excluded', self.joined)
        with override_settings(ANALYTICS_EXCLUDED_USER_IDS=[other.pk]):
            report = self.report()
        self.assertEqual(set(report['exclusions_sequential'].values()), {1})
        self.assertEqual(report['cohorts'][0]['population'], 1)
        # tzdata folder names raise IsADirectoryError from ZoneInfo.
        for tz in ['UTC', 'Invalid/Zone', 'America', 'Etc']:
            with self.subTest(tz=tz):
                Profile.objects.filter(pk=self.profile.pk).update(timezone=tz)
                self.assertEqual(self.report()['cohorts'][0]['timezone_fallback']['numerator'], 'suppressed')

    def test_timezone_matches_case_insensitively_like_pytz(self):
        # Signup at midnight UTC is March 8 in Tokyo but March 7 in Los Angeles;
        # 08:00 UTC on March 9 is local day 1 only in Tokyo.
        joined = datetime(2026, 3, 8, tzinfo=timezone.utc)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        self.event(EventType.DEVOTIONAL_VIEWED, datetime(2026, 3, 9, 8, tzinfo=timezone.utc))
        Profile.objects.filter(pk=self.profile.pk).update(timezone='asia/tokyo')
        report = self.report()
        self.assertEqual(report['cohorts'][0]['timezone_fallback']['numerator'], '<1')
        self.assertEqual(self.metric(report, 'd1')['numerator'], 'suppressed')

    @override_settings()
    def test_exclusion_lists_read_through_decouple(self):
        from django.conf import settings
        for name in ('ANALYTICS_EXCLUDED_USER_IDS', 'ANALYTICS_EXCLUDED_EMAIL_DOMAINS'):
            if hasattr(settings, name):
                delattr(settings, name)
        with mock.patch.dict(os.environ, {'ANALYTICS_EXCLUDED_EMAIL_DOMAINS': ' a.invalid, b.invalid '}):
            self.assertEqual(_configured('ANALYTICS_EXCLUDED_EMAIL_DOMAINS'), ['a.invalid', 'b.invalid'])
        with mock.patch('events.growth_cohorts.config', return_value='7,8') as config:
            self.assertEqual(_configured('ANALYTICS_EXCLUDED_USER_IDS'), ['7', '8'])
        config.assert_called_once_with('ANALYTICS_EXCLUDED_USER_IDS', default='')

    def test_legacy_join_dates_pair_with_undated_audit_row(self):
        # The backfill leaves joined_at NULL after repeated legacy joins; the
        # Event is then the pair's only dated join and must still activate.
        fast = self.create_fast()
        FastParticipation.objects.create(profile=self.profile, fast=fast, joined_at=None)
        self.event(EventType.USER_JOINED_FAST, self.joined + timedelta(hours=1), target=fast)
        report = self.report()
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 'suppressed')
        self.assertEqual(report['cohorts'][0]['first_action_within_7d'][EventType.USER_JOINED_FAST]['numerator'],
                         'suppressed')

    def test_authoritative_join_without_event_and_duplicate_legacy(self):
        fast = self.create_fast()
        FastParticipation.objects.create(profile=self.profile, fast=fast, joined_at=self.joined + timedelta(hours=1))
        self.event(EventType.USER_JOINED_FAST, self.joined + timedelta(hours=2), target=fast)
        self.event(EventType.USER_JOINED_FAST, self.joined, target=fast,
                   data={'participation_tracked': True, 'participation_changed': False})
        report = self.report()
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 'suppressed')
        self.assertEqual(report['cohorts'][0]['first_action_within_7d'][EventType.USER_JOINED_FAST]['numerator'], 'suppressed')
        # Audit authority overrides the earlier duplicate/no-op Event timestamp.
        FastParticipation.objects.filter(profile=self.profile).update(joined_at=self.joined + timedelta(days=2))
        self.assertEqual(self.metric(self.report(), 'activation_24h')['numerator'], '<1')

    def test_fallback_share_has_no_maturity_window(self):
        # setUp supplies one explicit LA profile; add 10 in each group.
        for index in range(20):
            user = self.make_user(f'fallback{index}', self.joined)
            profile, _ = Profile.objects.get_or_create(user=user)
            Profile.objects.filter(pk=profile.pk).update(
                timezone='UTC' if index < 10 else 'America/Los_Angeles')
        diagnostic = self.report(minimum=10)['cohorts'][0]['timezone_fallback']
        self.assertEqual(diagnostic['numerator'], 10)
        self.assertEqual(diagnostic['mature_denominator'], 21)
        self.assertNotIn('immature', diagnostic)
        self.assertEqual(diagnostic['percent'], 47.62)
        self.assertLess(diagnostic['wilson_95'][0], diagnostic['percent'])
        self.assertGreater(diagnostic['wilson_95'][1], diagnostic['percent'])

    def test_fallback_share_suppresses_small_cells(self):
        for n, expected in [(0, '<10'), (9, '<10'), (12, 'suppressed'), (21, 'suppressed')]:
            with self.subTest(fallback=n):
                diagnostic = _metric(n, 21, None, 10)
                self.assertEqual(diagnostic['numerator'], expected)
                self.assertEqual(diagnostic['mature_denominator'], 21)
                self.assertNotIn('immature', diagnostic)
                self.assertIsNone(diagnostic['percent'])
                self.assertIsNone(diagnostic['wilson_95'])

    def test_noise_invariance_property(self):
        self.event(EventType.PRAYER_SET_VIEWED, self.joined + timedelta(days=7))
        before = self.report()
        codes = [EventType.SCREEN_VIEW, EventType.APP_OPEN, EventType.SESSION_START, EventType.SESSION_END]
        for index in range(100):
            self.event(codes[index % len(codes)], self.joined + timedelta(minutes=index),
                       data={'source': 'client_ui', 'schema_version': 2})
        self.assertEqual(self.report(), before)

    def test_suppression_command_and_wilson(self):
        report = self.report(minimum=10)
        self.assertEqual(report['cohorts'][0]['population'], '<10')
        self.assertIsNone(self.metric(report, 'activation_24h')['percent'])
        metric = _metric(20, 40, 10, 10)
        self.assertEqual(metric['percent'], 50)
        self.assertLess(metric['wilson_95'][0], 50)
        self.assertGreater(metric['wilson_95'][1], 50)
        for fmt in ['json', 'md']:
            output = StringIO()
            call_command('growth_cohorts', as_of='2026-05-01', format=fmt, stdout=output)
            self.assertIn('data_freshness', output.getvalue())
            self.assertNotIn('username', output.getvalue())

    def test_complementary_count_suppression(self):
        for n, d, immature in [(19, 20, 10), (20, 20, 10)]:
            with self.subTest(n=n, d=d):
                metric = _metric(n, d, immature, 10)
                self.assertEqual(metric['numerator'], 'suppressed')
                self.assertEqual(metric['mature_denominator'], d)
                self.assertIsNone(metric['percent'])
                self.assertIsNone(metric['wilson_95'])
        for immature in (1, 9):
            with self.subTest(immature=immature):
                metric = _metric(10, 20, immature, 10)
                self.assertEqual(metric['numerator'], 'suppressed')
                self.assertEqual(metric['mature_denominator'], 'suppressed')
                self.assertEqual(metric['immature'], '<10')
                self.assertIsNone(metric['percent'])
                self.assertIsNone(metric['wilson_95'])
        self.assertEqual(_metric(10, 20, 10, 10)['percent'], 50)
        # Population 25 is released, so a visible immature 22 would give the
        # hidden denominator away as exactly 3.
        metric = _metric(2, 3, 22, 10)
        self.assertEqual(metric['mature_denominator'], '<10')
        self.assertEqual(metric['immature'], 'suppressed')
        self.assertEqual(metric['numerator'], '<10')

    def test_query_count_does_not_grow_with_population_or_weeks(self):
        with CaptureQueriesContext(connection) as baseline:
            self.report()
        for index in range(12):
            user = self.make_user(f'bounded{index}', self.joined - timedelta(weeks=index))
            profile, _ = Profile.objects.get_or_create(user=user)
            Profile.objects.filter(pk=profile.pk).update(timezone='America/Los_Angeles')
        with CaptureQueriesContext(connection) as expanded:
            self.report()
        self.assertEqual(len(baseline), len(expanded))
        # Four exclusion counts + population + three streams + freshness.
        self.assertLessEqual(sum(q['sql'].lstrip().upper().startswith('SELECT') for q in expanded), 9)

    def test_query_count_is_fixed_across_events_users_weeks_and_timezones(self):
        with CaptureQueriesContext(connection) as baseline:
            self.report()
        for index in range(12):
            joined = self.joined - timedelta(weeks=index)
            user = self.make_user(f'eventbounded{index}', joined)
            profile, _ = Profile.objects.get_or_create(user=user)
            Profile.objects.filter(pk=profile.pk).update(
                timezone='Asia/Tokyo' if index % 2 else 'America/Los_Angeles')
            for day in (1, 7, 30):
                event = self.event(EventType.DEVOTIONAL_VIEWED, joined + timedelta(days=day))
                Event.objects.filter(pk=event.pk).update(user=user)
        with CaptureQueriesContext(connection) as expanded:
            self.report()
        self.assertEqual(len(baseline), len(expanded))
        self.assertLessEqual(sum(q['sql'].lstrip().upper().startswith('SELECT') for q in expanded), 9)

    def test_multiple_timezones_keep_independent_local_days(self):
        joined = datetime(2026, 3, 8, tzinfo=timezone.utc)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        other = self.make_user('tokyo', joined)
        profile, _ = Profile.objects.get_or_create(user=other)
        Profile.objects.filter(pk=profile.pk).update(timezone='Asia/Tokyo')
        at = datetime(2026, 3, 9, 8, tzinfo=timezone.utc)
        self.event(EventType.DEVOTIONAL_VIEWED, at)
        event = self.event(EventType.DEVOTIONAL_VIEWED, at)
        Event.objects.filter(pk=event.pk).update(user=other)
        # Tokyo: March 8 -> March 9 (D1). LA: March 7 -> March 9 (D2).
        # Same UTC signup week, but this LA user's D1 has not closed yet.
        self.make_user('immature', datetime(2026, 3, 8, 23, tzinfo=timezone.utc))
        d1 = self.metric(self.report(date(2026, 3, 10)), 'd1')
        self.assertEqual(d1['numerator'], 1)
        self.assertEqual(d1['mature_denominator'], 2)
        self.assertEqual(d1['immature'], 1)

    def test_fall_dst_day30_tail_and_31_day_edge(self):
        joined = datetime(2026, 10, 31, 7, tzinfo=timezone.utc)  # LA midnight
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        # November 30 23:30 LA is 31 elapsed days + 30 minutes after signup.
        self.event(EventType.DEVOTIONAL_VIEWED, joined + timedelta(days=31, minutes=30))
        self.assertEqual(self.metric(self.report(date(2026, 12, 3)), 'd30')['numerator'], 'suppressed')
        # December 1 00:00 LA is day 31 and must not count.
        Event.objects.filter(user=self.user).delete()
        self.event(EventType.DEVOTIONAL_VIEWED, joined + timedelta(days=31, hours=1))
        self.assertEqual(self.metric(self.report(date(2026, 12, 3)), 'd30')['numerator'], '<1')

    def test_events_across_elapsed_31_day_edge(self):
        # Spring DST has already advanced the local clock by the endpoint.
        self.event(EventType.DEVOTIONAL_VIEWED, self.joined + timedelta(days=30))
        self.event(EventType.PRAYER_SET_VIEWED, self.joined + timedelta(days=31))
        self.event(EventType.PRAYER_SET_VIEWED, self.joined + timedelta(days=32))
        report = self.report()
        self.assertEqual(self.metric(report, 'd30')['numerator'], 'suppressed')
        self.assertEqual(self.metric(report, 'activation_7d')['numerator'], '<1')

    def test_participation_wins_event_tie_and_earlier_event_wins(self):
        at = self.joined + timedelta(hours=1)
        self.event(EventType.DEVOTIONAL_VIEWED, at)
        period = FastParticipation.objects.create(profile=self.profile, fast=self.target, joined_at=at)
        with mock.patch('events.growth_cohorts._suppress_distribution', wraps=_suppress_distribution) as distribution:
            self.report()
        self.assertEqual(distribution.call_args.args[1][EventType.USER_JOINED_FAST], 1)
        self.assertEqual(distribution.call_args.args[1][EventType.DEVOTIONAL_VIEWED], 0)
        FastParticipation.objects.filter(pk=period.pk).update(joined_at=at + timedelta(seconds=1))
        with mock.patch('events.growth_cohorts._suppress_distribution', wraps=_suppress_distribution) as distribution:
            self.report()
        self.assertEqual(distribution.call_args.args[1][EventType.DEVOTIONAL_VIEWED], 1)

    def test_event_ties_use_code_before_primary_key(self):
        codes = sorted([EventType.DEVOTIONAL_VIEWED, EventType.PRAYER_SET_VIEWED])
        for code in reversed(codes):
            self.event(code, self.joined + timedelta(hours=1))
        with mock.patch('events.growth_cohorts._suppress_distribution', wraps=_suppress_distribution) as distribution:
            self.report()
        self.assertEqual(distribution.call_args.args[1][codes[0]], 1)
        self.assertEqual(distribution.call_args.args[1][codes[1]], 0)

    def test_iterator_chunk_boundary_preserves_report_and_query_count(self):
        for index in range(5):
            self.event(EventType.DEVOTIONAL_VIEWED, self.joined + timedelta(days=index))
            user = self.make_user(f'chunk{index}', self.joined)
            profile, _ = Profile.objects.get_or_create(user=user)
            FastParticipation.objects.create(profile=profile, fast=self.target,
                                             joined_at=self.joined + timedelta(days=index))
        with CaptureQueriesContext(connection) as normal_queries:
            normal = self.report()
        with mock.patch('events.growth_cohorts.ITERATOR_CHUNK_SIZE', 2):
            with CaptureQueriesContext(connection) as chunked_queries:
                chunked = self.report()
        self.assertEqual(chunked, normal)
        self.assertEqual(len(normal_queries), len(chunked_queries))
        self.assertLessEqual(sum(q['sql'].lstrip().upper().startswith('SELECT') for q in chunked_queries), 9)

    def test_pre_signup_and_account_creation_do_not_activate(self):
        self.event(EventType.DEVOTIONAL_VIEWED, self.joined - timedelta(seconds=1))
        self.event(EventType.USER_ACCOUNT_CREATED, self.joined)
        self.assertEqual(self.metric(self.report(), 'activation_7d')['numerator'], '<1')

    def test_exact_day_and_any_return_endpoints(self):
        # Same local time avoids ambiguity; endpoint dates are inclusive.
        self.event(EventType.DEVOTIONAL_VIEWED, self.joined + timedelta(days=7))
        self.event(EventType.PRAYER_SET_VIEWED, self.joined + timedelta(days=30))
        report = self.report()
        for name in ['d7', 'd30', 'return_days_1_7', 'return_days_8_30']:
            self.assertEqual(self.metric(report, name)['numerator'], 'suppressed')
        self.assertEqual(self.metric(report, 'd1')['numerator'], '<1')

    def test_first_action_distribution_complementary_suppression(self):
        counts = {'a': 20, 'b': 20, 'c': 5}
        actions = {code: _metric(n, 45, 0, 10) for code, n in counts.items()}
        _suppress_distribution(actions, counts, 10)
        self.assertEqual(actions['c']['numerator'], '<10')
        self.assertEqual(actions['a']['numerator'], 'suppressed')
        self.assertIsNone(actions['a']['percent'])
        self.assertIsNone(actions['a']['wilson_95'])
        self.assertEqual(actions['b']['numerator'], 20)
        self.assertEqual(actions['b']['mature_denominator'], 45)
        # Hiding only the 10 would pin it at 10 and every '<10' cell at 0,
        # because 'suppressed' already says it is at least the threshold.
        counts = {'devotional': 30, 'prayer_set': 10, 'other': 0}
        actions = {code: _metric(n, 40, 0, 10) for code, n in counts.items()}
        _suppress_distribution(actions, counts, 10)
        for code in ('devotional', 'prayer_set'):
            self.assertEqual(actions[code]['numerator'], 'suppressed')

    def test_nested_window_complementary_suppression(self):
        metrics = {name: _metric(n, 80, 0, 10) for name, n in [
            ('activation_24h', 20), ('activation_72h', 25), ('activation_7d', 40),
            ('d1', 20), ('d7', 25), ('return_days_1_7', 30),
            ('d30', 20), ('return_days_8_30', 25)]}
        _suppress_nested(metrics, 10)
        for name in ['activation_72h', 'return_days_1_7', 'return_days_8_30']:
            self.assertEqual(metrics[name]['numerator'], 'suppressed')
            self.assertIsNone(metrics[name]['percent'])
            self.assertIsNone(metrics[name]['wilson_95'])
        self.assertEqual(metrics['activation_24h']['numerator'], 20)
        self.assertEqual(metrics['activation_7d']['numerator'], 40)
        # Different mature populations do not define a subtractable nested band.
        metrics['activation_72h'] = _metric(25, 70, 20, 10)
        _suppress_nested(metrics, 10)
        self.assertEqual(metrics['activation_72h']['numerator'], 25)
        metrics['activation_72h'] = _metric(20, 80, 10, 10)
        _suppress_nested(metrics, 10)
        self.assertEqual(metrics['activation_72h']['numerator'], 20)

    def test_command_min_cell_floor(self):
        for minimum in [0, 1, 9]:
            with self.subTest(minimum=minimum):
                with self.assertRaisesMessage(CommandError, '--min-cell must be at least 10'):
                    call_command('growth_cohorts', as_of='2026-05-01',
                                 min_cell=minimum, stdout=StringIO())
        output = StringIO()
        call_command('growth_cohorts', as_of='2026-05-01', min_cell=1,
                     unsafe_min_cell=True, stdout=output)
        self.assertIn('"min_cell": 1', output.getvalue())
