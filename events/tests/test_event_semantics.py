"""Read-time segregation preserves historical rows without counting GET noise."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from events.analytics_optimizer import AnalyticsQueryOptimizer
from events.event_semantics import legacy_screen_views, true_ui_screen_views
from events.management.commands.engagement_report import Command
from events.models import Event, EventType
from events.participation_analytics import analytics_events


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class EventSemanticsTests(TestCase):
    def setUp(self):
        EventType.get_or_create_default_types()
        self.viewer = get_user_model().objects.create_user(username='viewer')
        self.legacy_viewer = get_user_model().objects.create_user(username='legacy')
        self.screen_type = EventType.objects.get(code=EventType.SCREEN_VIEW)
        self.legacy = []
        for data in (
            {},
            {'source': 'api'},
            {'source': 'app_ui', 'schema_version': 2},
            {'source': 'client_ui'},
            {'schema_version': 2},
            {'source': 'client_ui', 'schema_version': 1},
            {'source': None, 'schema_version': None},
            {'source': 'unknown', 'schema_version': 2},
        ):
            self.legacy.append(Event.objects.create(
                event_type=self.screen_type, user=self.legacy_viewer,
                data={**data, 'screen': '/api/private/?secret=objectionable'},
            ))
        self.true_views = [Event.objects.create(
            event_type=self.screen_type, user=self.viewer,
            data={'source': 'client_ui', 'schema_version': version, 'screen': 'home'},
        ) for version in (2, 3)]
        self.other = Event.objects.create(
            event_type=EventType.objects.get(code=EventType.APP_OPEN), user=self.viewer,
            data={'source': 'client_ui', 'schema_version': 2},
        )

    def test_exact_disjoint_partition_and_queryset_scope(self):
        qs = Event.objects.all()
        original = list(qs.order_by('pk').values())
        true_ids = set(true_ui_screen_views(qs).values_list('pk', flat=True))
        legacy_ids = set(legacy_screen_views(qs).values_list('pk', flat=True))
        self.assertEqual(true_ids, {e.pk for e in self.true_views})
        self.assertEqual(legacy_ids, {e.pk for e in self.legacy})
        self.assertFalse(true_ids & legacy_ids)
        self.assertEqual(true_ids | legacy_ids, set(
            qs.filter(event_type=self.screen_type).values_list('pk', flat=True),
        ))
        self.assertFalse(true_ui_screen_views(qs.filter(user=self.legacy_viewer)).exists())
        self.assertFalse(legacy_screen_views(qs.filter(user=self.viewer)).exists())
        self.assertEqual(original, list(qs.order_by('pk').values()))

    def test_report_excludes_legacy_from_every_screen_metric(self):
        now = timezone.now()
        outside = Event.objects.create(event_type=self.screen_type, data={})
        Event.objects.filter(pk=outside.pk).update(timestamp=now - timedelta(days=10))
        metrics = Command()._compute_other_metrics(now - timedelta(days=1), now + timedelta(days=1))
        screens = metrics['screen_views']
        self.assertEqual(screens['total'], 2)
        self.assertEqual(screens['unique_users'], 1)
        self.assertEqual(screens['avg_per_user'], 2)
        self.assertEqual(screens['legacy_excluded'], len(self.legacy))
        self.assertIn('Legacy API-GET', screens['note'])
        self.assertEqual(screens['top_screens'], [{'screen': 'home', 'views': 2}])
        self.assertEqual(sum(screens['daily_timeline'].values()), 2)
        self.assertEqual(metrics['events_by_type'][EventType.SCREEN_VIEW], 2)

    def test_shared_aggregates_exclude_legacy_but_preserve_other_events(self):
        self.assertEqual(set(analytics_events().values_list('pk', flat=True)), {
            self.other.pk, *(event.pk for event in self.true_views),
        })
        start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = AnalyticsQueryOptimizer.get_daily_event_aggregates(
            start, 1, filters={'only_event_types': [EventType.SCREEN_VIEW]},
        )
        self.assertEqual(sum(result['events_by_day'].values()), 2)
