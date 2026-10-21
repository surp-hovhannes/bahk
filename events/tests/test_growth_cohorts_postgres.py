"""Exercise real snapshot transactions rather than TestCase's outer atomic block."""

from datetime import date, datetime, timedelta, timezone
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings

from events.event_schema import MEANINGFUL_EVENT_CODES
from events.growth_cohorts import CAVEATS, _build_report, build_report
from events.models import Event, EventType
from hub.models import Profile


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL snapshot isolation')
@override_settings(ANALYTICS_EXCLUDED_USER_IDS=[], ANALYTICS_EXCLUDED_EMAIL_DOMAINS=[])
class GrowthCohortPostgresTests(TransactionTestCase):
    def setUp(self):
        # TransactionTestCase flushes between tests; class-level types cannot survive.
        EventType.get_or_create_default_types()
        joined = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
        self.user = get_user_model().objects.create_user(username='postgres-cohort')
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=joined)
        profile, _ = Profile.objects.get_or_create(user=self.user)
        Profile.objects.filter(pk=profile.pk).update(timezone='America/Los_Angeles')
        # CHECKLIST_USED does not require a target; save() validates the title.
        event = Event.create_event(EventType.CHECKLIST_USED, user=self.user,
                                   title='Synthetic cohort checklist')
        Event.objects.filter(pk=event.pk).update(timestamp=joined + timedelta(hours=1))

    def test_top_level_report_matches_expected_aggregates(self):
        self.assertFalse(connection.in_atomic_block)
        report = build_report(date(2026, 5, 1), 1)
        # Same suppression and local-day expectations as the backend-independent suite:
        # one mature activated user, no return days, and no immature users.
        activated = {'numerator': 'suppressed', 'mature_denominator': 1,
                     'percent': None, 'wilson_95': None, 'immature': 0}
        no_return = {**activated, 'numerator': '<1'}
        self.assertEqual(report, {
            'as_of': '2026-05-01T00:00:00+00:00',
            'activation_data_through': '2026-04-30T00:00:00+00:00',
            'data_freshness': '2026-03-08T10:00:00+00:00',
            'min_cell': 1, 'granularity': 'week', 'cohort_timezone': 'UTC',
            'exclusions_sequential': dict.fromkeys(
                ['staff', 'superuser', 'configured_ids', 'configured_email_domains'], '<1'),
            'caveats': CAVEATS,
            'cohorts': [{
                'week_start': '2026-03-02', 'population': 1,
                'timezone_fallback': {'numerator': '<1', 'mature_denominator': 1,
                                      'percent': None, 'wilson_95': None},
                'metrics': {
                    **dict.fromkeys(['activation_24h', 'activation_72h', 'activation_7d'], activated),
                    **dict.fromkeys(['d1', 'd7', 'd30', 'return_days_1_7', 'return_days_8_30'], no_return),
                },
                'first_action_within_7d': {
                    code: {**(activated if code == EventType.CHECKLIST_USED else no_return),
                           'mature_denominator': 'suppressed'}
                    for code in MEANINGFUL_EVENT_CODES
                },
            }],
        })

    def test_top_level_transaction_is_repeatable_read_and_read_only(self):
        observed = []

        def capture_snapshot(*args):
            with connection.cursor() as cursor:
                cursor.execute('SHOW transaction_isolation')
                isolation = cursor.fetchone()[0]
                cursor.execute('SHOW transaction_read_only')
                observed.append((isolation, cursor.fetchone()[0]))
            return _build_report(*args)

        self.assertFalse(connection.in_atomic_block)
        with mock.patch('events.growth_cohorts._build_report', side_effect=capture_snapshot):
            build_report(date(2026, 5, 1), 1)
        self.assertEqual(observed, [('repeatable read', 'on')])

    def test_nested_report_after_prior_query_matches_top_level(self):
        before = build_report(date(2026, 5, 1), 1)
        with transaction.atomic():
            self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
            with mock.patch('events.growth_cohorts.transaction.atomic') as atomic:
                self.assertEqual(build_report(date(2026, 5, 1), 1), before)
            atomic.assert_not_called()
