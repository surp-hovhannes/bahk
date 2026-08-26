"""Admin contracts for navigating the content calendar."""

import datetime
from types import SimpleNamespace

from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase

from hub.admin import DayAdmin, DevotionalAdmin, ReadingAdmin
from hub.models import Day, Devotional, Reading


class CalendarAdminTests(SimpleTestCase):
    def setUp(self):
        self.day_admin = DayAdmin(Day, AdminSite())
        self.reading_admin = ReadingAdmin(Reading, AdminSite())
        self.devotional_admin = DevotionalAdmin(Devotional, AdminSite())

    def test_calendar_models_expose_date_navigation(self):
        self.assertEqual(self.day_admin.date_hierarchy, "date")
        self.assertEqual(self.reading_admin.date_hierarchy, "day__date")
        self.assertEqual(self.devotional_admin.date_hierarchy, "day__date")

    def test_day_search_accepts_date_church_and_fast(self):
        self.assertIn("=date", self.day_admin.search_fields)
        self.assertIn("church__name", self.day_admin.search_fields)
        self.assertIn("fast__name", self.day_admin.search_fields)

    def test_reading_filters_and_search_follow_the_related_day(self):
        self.assertIn("day__church", self.reading_admin.list_filter)
        self.assertIn("day__fast", self.reading_admin.list_filter)
        self.assertIn("=day__date", self.reading_admin.search_fields)
        self.assertIn("day__church__name", self.reading_admin.search_fields)

    def test_day_church_link_uses_authoritative_day_church(self):
        day = SimpleNamespace(
            pk=1,
            date=datetime.date(2026, 8, 26),
            church=SimpleNamespace(pk=17, name="Day Church"),
            fast=SimpleNamespace(
                pk=23,
                name="Fast",
                church=SimpleNamespace(pk=29, name="Different Fast Church"),
            ),
        )

        markup = str(self.day_admin.church_link(day))

        self.assertIn("Day Church", markup)
        self.assertIn("/17/change/", markup)
        self.assertNotIn("Different Fast Church", markup)
