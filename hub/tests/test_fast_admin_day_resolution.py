"""Tests for FastAdmin's (date, church)-keyed Day resolution + conflict guard.

Covers the one-fast-per-(date, church) invariant enforced by the
``unique_day_per_church`` constraint: reusing an existing readings Day,
adopting it onto a fast that lacks one, and refusing to steal a Day that
already belongs to a different fast.
"""

from datetime import date, timedelta

from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from hub.admin import FastAdmin
from hub.models import Church, Day, Fast


class FastAdminDayResolutionTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Test Church")
        self.other_church = Church.objects.create(name="Other Church")
        self.fast = Fast.objects.create(name="Fast A", church=self.church)
        self.admin = FastAdmin(Fast, AdminSite())
        self.d0 = date(2026, 3, 1)

    def test_creates_new_days_when_none_exist(self):
        dates = [self.d0, self.d0 + timedelta(days=1)]
        days, conflicts = self.admin._get_or_create_days_for_fast(self.fast, dates)

        self.assertEqual(conflicts, [])
        self.assertEqual([d.date for d in days], dates)
        self.assertTrue(all(d.church_id == self.church.pk for d in days))

    def test_reuses_existing_readings_day(self):
        """A pre-existing fast=None Day (e.g. from the readings API) is reused,
        not duplicated."""
        existing = Day.objects.create(date=self.d0, church=self.church)

        days, conflicts = self.admin._get_or_create_days_for_fast(self.fast, [self.d0])

        self.assertEqual(conflicts, [])
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].pk, existing.pk)
        self.assertEqual(Day.objects.filter(date=self.d0, church=self.church).count(), 1)

    def test_same_date_different_church_is_not_a_conflict(self):
        Day.objects.create(date=self.d0, church=self.other_church, fast=None)

        days, conflicts = self.admin._get_or_create_days_for_fast(self.fast, [self.d0])

        self.assertEqual(conflicts, [])
        self.assertEqual(days[0].church_id, self.church.pk)

    def test_day_owned_by_another_fast_is_reported_as_conflict(self):
        other_fast = Fast.objects.create(name="Fast B", church=self.church)
        Day.objects.create(date=self.d0, church=self.church, fast=other_fast)

        days, conflicts = self.admin._get_or_create_days_for_fast(self.fast, [self.d0])

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].date, self.d0)
        self.assertEqual(conflicts[0].fast_id, other_fast.pk)

    def test_day_already_owned_by_same_fast_is_not_a_conflict(self):
        Day.objects.create(date=self.d0, church=self.church, fast=self.fast)

        days, conflicts = self.admin._get_or_create_days_for_fast(self.fast, [self.d0])

        self.assertEqual(conflicts, [])
        self.assertEqual(days[0].fast_id, self.fast.pk)
