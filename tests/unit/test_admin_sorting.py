"""Tests for useful sort controls on custom admin columns."""

from django.test import SimpleTestCase

from hub.admin import DayAdmin, DevotionalSetAdmin, FastAdmin, PassageTextAdmin, ProfileAdmin
from prayers.admin import FeastPrayerAdmin, PrayerRequestAdmin, PrayerSetAdmin


class AdminSortingTests(SimpleTestCase):
    def test_custom_relational_columns_declare_ordering(self):
        self.assertEqual(DayAdmin.church_link.admin_order_field, "church__name")
        self.assertEqual(DayAdmin.fast_link.admin_order_field, "fast__name")
        self.assertEqual(ProfileAdmin.church_link.admin_order_field, "church__name")
        self.assertEqual(FastAdmin.church_link.admin_order_field, "church__name")

    def test_annotated_count_columns_declare_ordering(self):
        self.assertEqual(
            DevotionalSetAdmin.number_of_days.admin_order_field,
            "_admin_devotional_count",
        )
        self.assertEqual(PrayerSetAdmin.prayer_count.admin_order_field, "_admin_prayer_count")
        self.assertEqual(
            PrayerRequestAdmin.acceptance_count.admin_order_field,
            "_admin_acceptance_count",
        )
        self.assertEqual(
            PassageTextAdmin.readings_served.admin_order_field,
            "_admin_readings_served",
        )

    def test_truncated_feast_prayer_columns_remain_sortable(self):
        self.assertEqual(FeastPrayerAdmin.designation_short.admin_order_field, "designation")
        self.assertEqual(FeastPrayerAdmin.title_preview.admin_order_field, "title")
