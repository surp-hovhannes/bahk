"""Regression tests for the Content & Calendar admin queryset shape."""

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, SimpleTestCase

from hub.admin import DayAdmin, DevotionalSetAdmin, PassageTextAdmin
from hub.models import Day, DevotionalSet, PassageText
from icons.admin import IconAdmin
from icons.models import Icon
from prayers.admin import PrayerRequestAdmin, PrayerSetAdmin
from prayers.models import PrayerRequest, PrayerSet


class AdminChangelistQueryTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.site = AdminSite()

    def test_related_objects_are_eager_loaded_for_representative_lists(self):
        day_queryset = DayAdmin(Day, self.site).get_queryset(self.request)
        icon_queryset = IconAdmin(Icon, self.site).get_queryset(self.request)

        self.assertIn("church", day_queryset.query.select_related)
        self.assertIn("fast", day_queryset.query.select_related)
        self.assertIn("readings", day_queryset._prefetch_related_lookups)
        self.assertIn("church", icon_queryset.query.select_related)
        self.assertIn("tags", icon_queryset._prefetch_related_lookups)

    def test_counts_are_annotations_instead_of_per_row_queries(self):
        devotional_sets = DevotionalSetAdmin(DevotionalSet, self.site).get_queryset(self.request)
        prayer_sets = PrayerSetAdmin(PrayerSet, self.site).get_queryset(self.request)
        prayer_requests = PrayerRequestAdmin(PrayerRequest, self.site).get_queryset(self.request)
        passage_texts = PassageTextAdmin(PassageText, self.site).get_queryset(self.request)

        self.assertIn("_admin_devotional_count", devotional_sets.query.annotations)
        self.assertIn("_admin_prayer_count", prayer_sets.query.annotations)
        self.assertIn("_admin_acceptance_count", prayer_requests.query.annotations)
        self.assertIn("_admin_prayer_log_count", prayer_requests.query.annotations)
        self.assertIn("_admin_readings_served", passage_texts.query.annotations)
