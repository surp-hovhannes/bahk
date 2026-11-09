"""Count and query regressions for the lightweight prayer-set listing."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.test import APITestCase

from hub.models import Church
from prayers.models import Prayer, PrayerSet, PrayerSetMembership
from prayers.serializers import PrayerSetListSerializer
from prayers.views import PrayerSetListView


class TwoPerPagePagination(PageNumberPagination):
    page_size = 2


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class PrayerSetListCountTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name='Count church')
        cls.other_church = Church.objects.create(name='Other count church')
        cls.user = get_user_model().objects.create_user(username='count-reader')
        cls.sets = []
        cls.counts = {}
        for index, count in enumerate((0, 1, 3, 2)):
            prayer_set = PrayerSet.objects.create(
                title=f'Count collection {index}',
                description='Searchable description',
                church=cls.other_church if index == 3 else cls.church,
                category='evening' if index == 2 else 'morning',
            )
            PrayerSet.objects.filter(pk=prayer_set.pk).update(
                created_at=timezone.now() + timedelta(minutes=index)
            )
            cls.sets.append(prayer_set)
            cls.counts[prayer_set.pk] = count
            for position in range(count):
                prayer = Prayer.objects.create(
                    title=f'Member {index}/{position}', text='Body must not be loaded',
                    church=cls.other_church, category='general',
                )
                PrayerSetMembership.objects.create(
                    prayer_set=prayer_set, prayer=prayer, order=position
                )
        # A shared prayer counts independently in each set.
        cls.sets[1].prayers.add(cls.sets[2].prayers.first())
        cls.counts[cls.sets[1].pk] = 2

    def get_list(self, params=None):
        response = self.client.get(reverse('prayers:prayer-set-list'), params or {})
        self.assertEqual(response.status_code, 200)
        for row in response.data['results']:
            self.assertEqual(row['prayer_count'], self.counts[row['id']])
            self.assertNotIn('prayers', row)
        return response.data

    def test_anonymous_and_authenticated_visibility_and_counts(self):
        anonymous = self.get_list()
        self.assertEqual(anonymous['count'], 4)
        self.assertEqual([row['id'] for row in anonymous['results']],
                         [item.pk for item in reversed(self.sets)])
        self.client.force_authenticate(self.user)
        authenticated = self.get_list()
        self.assertEqual(
            [(row['id'], row['prayer_count']) for row in anonymous['results']],
            [(row['id'], row['prayer_count']) for row in authenticated['results']],
        )

    def test_filters_preserve_complete_membership_counts(self):
        cases = [
            ({'church': self.church.pk}, self.sets[:3]),
            ({'category': 'evening'}, [self.sets[2]]),
            ({'search': 'collection 1'}, [self.sets[1]]),
            ({'search': 'searchable'}, self.sets),
            ({'church': self.church.pk, 'category': 'morning', 'search': 'collection'}, self.sets[:2]),
            ({'church': 'invalid'}, []),
            ({'search': 'missing'}, []),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                data = self.get_list(params)
                self.assertEqual(data['count'], len(expected))
                self.assertEqual({row['id'] for row in data['results']}, {item.pk for item in expected})

    def test_pagination_preserves_counts_and_order(self):
        with patch.object(PrayerSetListView, 'pagination_class', TwoPerPagePagination):
            first = self.get_list()
            second = self.get_list({'page': 2})
        self.assertEqual(first['count'], 4)
        self.assertEqual(second['count'], 4)
        self.assertIsNotNone(first['next'])
        self.assertIsNone(second['next'])
        self.assertEqual([row['id'] for row in first['results'] + second['results']],
                         [item.pk for item in reversed(self.sets)])

    def test_query_count_is_constant_without_loading_prayer_bodies(self):
        queries = []
        for params, expected in [({'search': 'collection 2'}, 1), ({}, 4)]:
            with CaptureQueriesContext(connection) as captured, patch.object(
                Prayer, 'from_db', side_effect=AssertionError('Listing must not load prayer objects')
            ):
                data = self.get_list(params)
            self.assertEqual(len(data['results']), expected)
            queries.append(captured.captured_queries)
        self.assertEqual(len(queries[0]), len(queries[1]))
        for captured in queries:
            listing = [query['sql'] for query in captured if 'prayers_prayerset' in query['sql'].lower()]
            self.assertEqual(len(listing), 2)  # Pagination total and the annotated page.
            self.assertTrue(any('COUNT(' in sql.upper() for sql in listing))
            for query in captured:
                self.assertNotIn('"prayers_prayer"."text"', query['sql'])
                self.assertNotIn('"prayers_prayer"."i18n"', query['sql'])
                self.assertNotIn('"prayers_prayer"."title"', query['sql'])

    def test_unannotated_serializer_fallback(self):
        for prayer_set in (self.sets[0], self.sets[2]):
            with self.subTest(prayer_set=prayer_set.pk):
                data = PrayerSetListSerializer(prayer_set).data
                self.assertEqual(data['prayer_count'], self.counts[prayer_set.pk])
