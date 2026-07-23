"""Tests for ``DevotionalAdmin.create_combined_devotional`` Day resolution.

This admin flow creates devotionals on a ``(date, church)`` Day. It must honor
the same one-fast-per-(date, church) invariant as the FastAdmin flows by routing
through ``get_or_create_days_for_fast``:

* reuse an existing ``fast=None`` Day (e.g. one the readings API created) and
  *adopt* it onto the selected fast, so ``day.fast`` is set — otherwise the new
  devotional is hidden from admin fast filtering and never triggers
  ``DevotionalSet`` cache invalidation (which short-circuits on ``day.fast``);
* refuse to *steal* a Day already owned by a different fast — the edge case a
  naive ``fast.days.add(day)`` would silently get wrong.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from hub.models import Church, Day, Devotional, Fast
from learning_resources.models import Video


class CreateCombinedDevotionalDayResolutionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.admin_user)

        self.church = Church.objects.create(name="Test Church")
        self.fast = Fast.objects.create(name="Fast A", church=self.church)
        self.d0 = date(2026, 3, 1)

        # Reuse an existing video so the form doesn't require a file upload.
        self.video = Video.objects.create(
            title="Existing devotional video",
            description="desc",
            category="devotional",
            video="videos/existing.mp4",
        )

        self.url = reverse("admin:create-combined-devotional")

    def _post_data(self, **overrides):
        data = {
            "date": self.d0.isoformat(),
            "fast": self.fast.pk,
            "languages": ["en"],
            "order": "",
            "existing_video_en": self.video.pk,
        }
        data.update(overrides)
        return data

    def test_adopts_existing_readings_day_onto_fast(self):
        """A pre-existing fast=None Day is reused *and* adopted onto the fast,
        so the devotional's day.fast is set (fixes the silent fast=None bug)."""
        existing = Day.objects.create(date=self.d0, church=self.church)
        self.assertIsNone(existing.fast_id)

        response = self.client.post(self.url, self._post_data())

        # Successful creation redirects to the new devotional's change page.
        self.assertEqual(response.status_code, 302)

        # No duplicate Day; the existing one was reused and adopted.
        self.assertEqual(
            Day.objects.filter(date=self.d0, church=self.church).count(), 1
        )
        existing.refresh_from_db()
        self.assertEqual(existing.fast_id, self.fast.pk)

        # Devotional was created on that same Day, and it now carries the fast
        # association that admin filtering + cache invalidation depend on.
        devotional = Devotional.objects.get(day=existing, language_code="en")
        self.assertEqual(devotional.day.fast_id, self.fast.pk)

    def test_new_day_is_created_and_associated_with_fast(self):
        response = self.client.post(self.url, self._post_data())

        self.assertEqual(response.status_code, 302)
        day = Day.objects.get(date=self.d0, church=self.church)
        self.assertEqual(day.fast_id, self.fast.pk)
        self.assertTrue(Devotional.objects.filter(day=day).exists())

    def test_does_not_steal_day_owned_by_a_different_fast(self):
        """The edge case cursor's autofix glossed over: a naive
        ``fast.days.add(day)`` would re-point a Day already owned by another
        fast. The flow must instead refuse, leave the Day untouched, and create
        no devotional."""
        other_fast = Fast.objects.create(name="Fast B", church=self.church)
        owned_day = Day.objects.create(
            date=self.d0, church=self.church, fast=other_fast
        )

        response = self.client.post(self.url, self._post_data())

        # Re-renders the form (no redirect) and surfaces an error message.
        self.assertEqual(response.status_code, 200)
        messages = list(response.context["messages"])
        self.assertTrue(
            any("already belongs to a different fast" in str(m) for m in messages),
            msg=f"expected a conflict error, got: {[str(m) for m in messages]}",
        )

        # The other fast's Day is untouched and no devotional was created.
        owned_day.refresh_from_db()
        self.assertEqual(owned_day.fast_id, other_fast.pk)
        self.assertFalse(Devotional.objects.filter(day=owned_day).exists())
        self.assertEqual(
            Day.objects.filter(date=self.d0, church=self.church).count(), 1
        )
