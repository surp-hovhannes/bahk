from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin import AdminSite
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from events.admin import EventAdmin
from events.analytics_optimizer import AnalyticsQueryOptimizer
from events.management.commands.engagement_report import Command
from events.models import Event, EventType
from events.participation_analytics import analytics_events, transition_counts
from hub.models import FastParticipation, Profile
from tests.fixtures.test_data import TestDataFactory


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class ParticipationAnalyticsTests(TestCase):
    def setUp(self):
        EventType.get_or_create_default_types()
        self.profile = TestDataFactory.create_profile()
        self.fast = TestDataFactory.create_fast(church=self.profile.church)
        self.milestones = patch('events.tasks.track_fast_participant_milestone_task.delay').start()
        self.addCleanup(patch.stopall)

    def counts(self, **kwargs):
        return transition_counts(**kwargs).get(None, {'joins': 0, 'leaves': 0})

    def test_real_join_leave_rejoin_counts_operations_not_current_members(self):
        self.profile.fasts.add(self.fast)
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        self.profile.fasts.remove(self.fast)
        self.profile.fasts.add(self.fast)
        self.assertEqual(self.counts(), {'joins': 2, 'leaves': 1})
        self.assertEqual(self.fast.profiles.count(), 1)
        self.assertEqual(FastParticipation.objects.count(), 2)
        events = Event.objects.filter(event_type__code__in=[EventType.USER_JOINED_FAST, EventType.USER_LEFT_FAST])
        self.assertEqual(events.count(), 3)

    def test_nonmember_remove_emits_no_event_or_transition(self):
        self.profile.fasts.remove(self.fast)
        self.assertEqual(self.counts(), {'joins': 0, 'leaves': 0})
        self.assertFalse(Event.objects.filter(event_type__code=EventType.USER_LEFT_FAST).exists())

    def test_reverse_multi_profile_changes_link_each_actual_period(self):
        other = TestDataFactory.create_profile(church=self.profile.church)
        self.fast.profiles.add(self.profile, other)
        joins = Event.objects.filter(event_type__code=EventType.USER_JOINED_FAST)
        for event in joins:
            period = FastParticipation.objects.get(pk=event.data['participation_id'])
            self.assertEqual(period.profile.user_id, event.user_id)
        self.assertEqual(joins.count(), 2)
        self.fast.profiles.clear()
        self.fast.profiles.clear()
        self.assertEqual(self.counts(), {'joins': 2, 'leaves': 2})
        self.assertEqual(Event.objects.filter(event_type__code=EventType.USER_LEFT_FAST).count(), 2)

    def test_history_counts_clear_even_when_legacy_leave_event_is_missing(self):
        self.profile.fasts.add(self.fast)
        self.profile.fasts.clear()
        Event.objects.filter(event_type__code=EventType.USER_LEFT_FAST).delete()
        with self.assertNumQueries(4):
            self.assertEqual(self.counts(), {'joins': 1, 'leaves': 1})
        now = timezone.now()
        daily = AnalyticsQueryOptimizer.get_daily_event_aggregates(now - timedelta(days=1), 2)
        self.assertEqual(sum(daily['fast_joins_by_day'].values()), 1)
        self.assertEqual(sum(daily['fast_leaves_by_day'].values()), 1)

    def test_audit_and_duplicate_legacy_events_are_not_double_counted(self):
        self.profile.fasts.add(self.fast)
        Event.create_event(event_type_code=EventType.USER_JOINED_FAST, user=self.profile.user,
                           target=self.fast, data={})
        self.assertEqual(self.counts(), {'joins': 1, 'leaves': 0})

    def test_legacy_fallback_and_key_safe_noop_exclusion(self):
        valid = Event.create_event(event_type_code=EventType.USER_JOINED_FAST, user=self.profile.user,
                                   target=self.fast, data={})
        noop = Event.create_event(event_type_code=EventType.USER_LEFT_FAST, user=self.profile.user,
                                  target=self.fast, data={'participation_tracked': True,
                                                        'participation_changed': False})
        unrelated = Event.create_event(event_type_code=EventType.USER_LOGGED_IN, user=self.profile.user,
                                       data={'participation_tracked': True, 'participation_changed': False})
        self.assertEqual(self.counts(), {'joins': 1, 'leaves': 0})
        self.assertTrue(analytics_events().filter(pk=valid.pk).exists())
        self.assertFalse(analytics_events().filter(pk=noop.pk).exists())
        self.assertTrue(analytics_events().filter(pk=unrelated.pk).exists())

    def test_unknown_timestamps_do_not_become_dated_transitions(self):
        FastParticipation.objects.create(profile=self.profile, fast=self.fast,
                                         joined_at=None, ended_at_unknown=True)
        Event.create_event(event_type_code=EventType.USER_JOINED_FAST, user=self.profile.user, target=self.fast)
        self.assertEqual(self.counts(), {'joins': 0, 'leaves': 0})

    def test_category_type_and_staff_filters_apply_to_audit_history(self):
        self.profile.fasts.add(self.fast)
        kind = EventType.objects.get(code=EventType.USER_JOINED_FAST)
        kind.category = 'custom_category'
        kind.save(update_fields=['category'])
        self.assertEqual(self.counts(filters={'include_categories': ['custom_category']})['joins'], 1)
        self.assertEqual(self.counts(filters={'exclude_categories': ['custom_category']})['joins'], 0)
        self.assertEqual(self.counts(filters={'only_event_types': [EventType.USER_LEFT_FAST]})['joins'], 0)
        self.profile.user.is_staff = True
        self.profile.user.save(update_fields=['is_staff'])
        self.assertEqual(self.counts(filters={'exclude_staff': True})['joins'], 0)

    def test_deleted_fast_snapshots_and_profile_privacy(self):
        self.profile.fasts.add(self.fast)
        fast_id = self.fast.pk
        self.fast.delete()
        self.assertEqual(self.counts(fast_id=fast_id)['joins'], 1)
        self.profile.delete()
        self.assertEqual(self.counts(fast_id=fast_id)['joins'], 0)

    def test_engagement_report_preserves_rejoin_periods(self):
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)
        self.profile.fasts.add(self.fast)
        now = timezone.now()
        rows = Command()._compute_user_fast_participation(now - timedelta(days=1), now + timedelta(days=1))
        rows = [row for row in rows if row.user_id == self.profile.user_id]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.status for row in rows}, {'left', 'active'})

    def test_admin_dashboard_suppresses_known_noops_in_all_summaries(self):
        Event.objects.all().delete()
        Event.create_event(
            event_type_code=EventType.USER_LEFT_FAST, user=self.profile.user, target=self.fast,
            data={'participation_tracked': True, 'participation_changed': False},
        )
        admin_user = TestDataFactory.create_user()
        admin_user.is_staff = admin_user.is_superuser = True
        admin_user.save(update_fields=['is_staff', 'is_superuser'])
        request = RequestFactory().get('/admin/events/event/analytics/', {'days': 7})
        request.user = admin_user
        response = EventAdmin(Event, AdminSite()).analytics_view(request)
        context = response.context_data
        self.assertEqual(context['total_events'], 0)
        self.assertEqual(context['events_in_period'], 0)
        self.assertTrue(all(row['count'] == 0 for row in context['events_by_type']))
        self.assertFalse(context['top_users'])
        self.assertFalse(any(context['hourly_data'].values()))

    def test_recreated_profile_does_not_resurrect_deleted_linked_history(self):
        self.profile.fasts.add(self.fast)
        user, church = self.profile.user, self.profile.church
        self.profile.delete()
        Profile.objects.create(user=user, church=church)
        self.assertEqual(self.counts(), {'joins': 0, 'leaves': 0})

    def test_unlinked_known_leave_can_fall_back_without_a_period(self):
        Event.create_event(
            event_type_code=EventType.USER_LEFT_FAST, user=self.profile.user, target=self.fast,
            data={'participation_tracked': True, 'participation_changed': True, 'participation_id': None},
        )
        self.assertEqual(self.counts(), {'joins': 0, 'leaves': 1})
