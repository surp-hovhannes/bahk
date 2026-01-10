"""Tests for prayer view analytics tracking."""

from rest_framework import status

from events.models import Event, EventType
from prayers.models import Prayer
from tests.base import BaseAPITestCase


class PrayerViewTrackingTests(BaseAPITestCase):
    def test_authenticated_prayer_detail_creates_prayer_viewed_event(self):
        church = self.create_church(name="Tracking Church")
        user = self.create_user(email="prayerviewer@example.com")
        self.authenticate(user)

        prayer = Prayer.objects.create(
            title="Test Prayer",
            text="Lord have mercy.",
            category="general",
            church=church,
            fast=None,
        )

        initial_count = Event.objects.filter(
            event_type__code=EventType.PRAYER_VIEWED,
            user=user,
            object_id=prayer.id,
        ).count()

        response = self.client.get(f"/api/prayers/{prayer.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(
            Event.objects.filter(
                event_type__code=EventType.PRAYER_VIEWED,
                user=user,
                object_id=prayer.id,
            ).count(),
            initial_count + 1,
        )

    def test_unauthenticated_prayer_detail_does_not_create_event(self):
        church = self.create_church(name="Public Church")

        prayer = Prayer.objects.create(
            title="Public Prayer",
            text="Amen.",
            category="general",
            church=church,
            fast=None,
        )

        response = self.client.get(f"/api/prayers/{prayer.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertFalse(
            Event.objects.filter(
                event_type__code=EventType.PRAYER_VIEWED,
                object_id=prayer.id,
            ).exists()
        )

