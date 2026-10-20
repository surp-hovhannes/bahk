from datetime import date, datetime, timedelta, timezone
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from events.growth_cohorts import _metric, build_report
from events.models import Event, EventType
from hub.models import FastParticipation, Profile


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class GrowthCohortTests(TestCase):
    def setUp(self):
        EventType.get_or_create_default_types()
        self.joined = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
        self.user = self.make_user('cohort', self.joined)
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        Profile.objects.filter(pk=self.profile.pk).update(timezone='America/Los_Angeles')

    def make_user(self, name, joined, **kwargs):
        user = get_user_model().objects.create_user(username=name, **kwargs)
        get_user_model().objects.filter(pk=user.pk).update(date_joined=joined)
        return user

    def event(self, code, at, **kwargs):
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
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 1)
        self.assertEqual(self.metric(report, 'd1')['numerator'], 1)
        self.assertEqual(self.metric(report, 'd7')['numerator'], '<1')

    def test_activation_and_closed_day_maturity_boundaries(self):
        joined = datetime(2026, 3, 8, tzinfo=timezone.utc)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        self.event(EventType.DEVOTIONAL_VIEWED, joined + timedelta(hours=24))
        # March 10 midnight minus 24h lag closes the 24h window exactly.
        report = self.report(date(2026, 3, 10))
        self.assertEqual(self.metric(report, 'activation_24h')['mature_denominator'], 1)
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 1)
        self.assertEqual(self.metric(report, 'activation_72h')['immature'], 1)
        self.assertEqual(self.metric(self.report(date(2026, 3, 9)), 'activation_24h')['immature'], 1)
        # Signup is March 7 locally; March 8 has closed by March 10 UTC.
        self.assertEqual(self.metric(report, 'd1')['mature_denominator'], 1)
        self.assertEqual(self.metric(self.report(date(2026, 3, 9)), 'd1')['immature'], 1)

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
        for tz in ['UTC', 'Invalid/Zone']:
            Profile.objects.filter(pk=self.profile.pk).update(timezone=tz)
            self.assertEqual(self.report()['cohorts'][0]['timezone_fallback']['numerator'], 1)

    def test_authoritative_join_without_event_and_duplicate_legacy(self):
        from tests.fixtures.test_data import TestDataFactory
        fast = TestDataFactory.create_fast()
        FastParticipation.objects.create(profile=self.profile, fast=fast, joined_at=self.joined + timedelta(hours=1))
        self.event(EventType.USER_JOINED_FAST, self.joined + timedelta(hours=2), target=fast)
        self.event(EventType.USER_JOINED_FAST, self.joined, target=fast,
                   data={'participation_tracked': True, 'participation_changed': False})
        report = self.report()
        self.assertEqual(self.metric(report, 'activation_24h')['numerator'], 1)
        self.assertEqual(report['cohorts'][0]['first_action_within_7d'][EventType.USER_JOINED_FAST]['numerator'], 1)
        # Audit authority overrides the earlier duplicate/no-op Event timestamp.
        FastParticipation.objects.filter(profile=self.profile).update(joined_at=self.joined + timedelta(days=2))
        self.assertEqual(self.metric(self.report(), 'activation_24h')['numerator'], '<1')

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
        metric = _metric(20, 40, 0, 10)
        self.assertEqual(metric['percent'], 50)
        self.assertLess(metric['wilson_95'][0], 50)
        self.assertGreater(metric['wilson_95'][1], 50)
        for fmt in ['json', 'md']:
            output = StringIO()
            call_command('growth_cohorts', as_of='2026-05-01', format=fmt, stdout=output)
            self.assertIn('data_freshness', output.getvalue())
            self.assertNotIn('username', output.getvalue())

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
            self.assertEqual(self.metric(report, name)['numerator'], 1)
        self.assertEqual(self.metric(report, 'd1')['numerator'], '<1')
