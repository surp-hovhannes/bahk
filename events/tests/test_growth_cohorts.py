import os
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from events.growth_cohorts import _configured, _metric, _suppress_distribution, _suppress_nested, build_report
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
        self.assertEqual(self.metric(report, 'activation_24h')['mature_denominator'], 'suppressed')
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 'suppressed')
        # Immature users leave no mature denominator; the immature cell is then
        # hidden too, since population minus it would recover the denominator.
        self.assertEqual(self.metric(report, 'activation_72h')['mature_denominator'], '<1')
        self.assertEqual(self.metric(report, 'activation_72h')['immature'], 'suppressed')
        self.assertEqual(self.metric(self.report(date(2026, 3, 9)), 'activation_24h')['mature_denominator'], '<1')
        # Signup is March 7 locally; March 8 has closed by March 10 UTC.
        self.assertEqual(self.metric(report, 'd1')['mature_denominator'], 'suppressed')
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
        for immature in (0, 1, 9):
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
        # Four exclusion counts + population + two streams + freshness.
        self.assertLessEqual(len(expanded), 8)

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
        self.assertLessEqual(len(expanded), 8)

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
        with mock.patch('events.growth_cohorts._metric', wraps=_metric) as metric:
            self.report()
        self.assertEqual(metric.call_args_list[3].args[:3], (1, 2, 0))

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
        self.assertLessEqual(len(chunked_queries), 8)

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
        actions = {code: _metric(n, 45, 10, 10) for code, n in counts.items()}
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
        actions = {code: _metric(n, 40, 10, 10) for code, n in counts.items()}
        _suppress_distribution(actions, counts, 10)
        for code in ('devotional', 'prayer_set'):
            self.assertEqual(actions[code]['numerator'], 'suppressed')

    def test_nested_window_complementary_suppression(self):
        metrics = {name: _metric(n, 80, 10, 10) for name, n in [
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
