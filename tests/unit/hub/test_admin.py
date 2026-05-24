"""Unit tests for hub admin actions."""
import datetime

from django.contrib.admin.sites import AdminSite

from hub.admin import DevotionalSetAdmin
from hub.models import Day, Devotional, DevotionalSet, Fast
from learning_resources.models import Video
from tests.base import BaseTestCase


class DuplicateDevotionalSetAdminTests(BaseTestCase):
    """Tests for DevotionalSetAdmin.duplicate_devotional_sets action."""

    def setUp(self):
        self.church = self.create_church()
        self.admin = DevotionalSetAdmin(DevotionalSet, AdminSite())

        current_year = datetime.date.today().year
        prev_year = current_year - 1

        # Source fast (previous year, 5 days)
        self.old_fast = Fast.objects.create(name="Fast of Elijah", church=self.church)
        for i in range(5):
            Day.objects.create(
                date=datetime.date(prev_year, 6, 16 + i),
                fast=self.old_fast,
                church=self.church,
            )
        self.old_fast.save(update_fields=["year"])

        # Correct target (current year, same name, same day count, no DevotionalSet)
        self.target_fast = Fast.objects.create(name="Fast of Elijah", church=self.church)
        for i in range(5):
            Day.objects.create(
                date=datetime.date(current_year, 6, 16 + i),
                fast=self.target_fast,
                church=self.church,
            )
        self.target_fast.save(update_fields=["year"])

        # Red herring: same year, same day count, DIFFERENT name — must be excluded
        self.other_fast = Fast.objects.create(name="Fast of the Assumption", church=self.church)
        for i in range(5):
            Day.objects.create(
                date=datetime.date(current_year, 8, 1 + i),
                fast=self.other_fast,
                church=self.church,
            )
        self.other_fast.save(update_fields=["year"])

        # Source DevotionalSet
        self.old_ds = DevotionalSet.objects.create(
            title=f"Fast of Elijah {prev_year}",
            fast=self.old_fast,
        )

        # Videos and devotionals for the source fast
        self.videos = [
            Video.objects.create(title=f"Elijah Day {i + 1}", category="devotional")
            for i in range(5)
        ]
        old_days = list(Day.objects.filter(fast=self.old_fast).order_by("date"))
        for day, video in zip(old_days, self.videos):
            Devotional.objects.create(day=day, video=video, order=1, language_code="en")

    # --- _find_target_fast ---

    def test_find_target_fast_ignores_same_length_different_name(self):
        """The name filter excludes same-length fasts whose names don't match."""
        result = self.admin._find_target_fast(self.old_fast)
        self.assertEqual(result, self.target_fast)

    def test_find_target_fast_raises_when_no_name_match(self):
        """Raises ValueError when no current-year fast shares the base name."""
        self.target_fast.delete()
        with self.assertRaises(ValueError):
            self.admin._find_target_fast(self.old_fast)

    def test_find_target_fast_raises_when_target_already_has_devotional_set(self):
        """Raises ValueError when the matching fast already has a DevotionalSet."""
        DevotionalSet.objects.create(title="Existing", fast=self.target_fast)
        with self.assertRaises(ValueError):
            self.admin._find_target_fast(self.old_fast)

    def test_find_target_fast_raises_when_day_count_differs(self):
        """Raises ValueError when the only name-matching fast has a different day count."""
        Day.objects.create(
            date=datetime.date(self.target_fast.year, 6, 25),
            fast=self.target_fast,
            church=self.church,
        )  # now target has 6 days vs source's 5
        with self.assertRaises(ValueError):
            self.admin._find_target_fast(self.old_fast)

    # --- _duplicate_devotional_set ---

    def test_duplicate_creates_devotional_set_for_target_fast(self):
        """A new DevotionalSet linked to the target fast is created."""
        self.admin._duplicate_devotional_set(self.old_ds)
        self.assertTrue(DevotionalSet.objects.filter(fast=self.target_fast).exists())

    def test_duplicate_updates_year_in_title(self):
        """The previous year in the title is replaced with the current year."""
        self.admin._duplicate_devotional_set(self.old_ds)
        new_ds = DevotionalSet.objects.get(fast=self.target_fast)
        current_year = str(datetime.date.today().year)
        self.assertIn(current_year, new_ds.title)
        self.assertNotIn(str(datetime.date.today().year - 1), new_ds.title)

    def test_duplicate_creates_correct_devotional_count(self):
        """One new Devotional is created for each day in the target fast."""
        self.admin._duplicate_devotional_set(self.old_ds)
        count = Devotional.objects.filter(day__fast=self.target_fast).count()
        self.assertEqual(count, 5)

    def test_duplicate_reuses_videos(self):
        """New Devotionals point to the same Video objects — none are created."""
        video_count_before = Video.objects.count()
        self.admin._duplicate_devotional_set(self.old_ds)
        self.assertEqual(Video.objects.count(), video_count_before)

        new_devotionals = Devotional.objects.filter(day__fast=self.target_fast)
        self.assertEqual(
            {d.video_id for d in new_devotionals},
            {v.pk for v in self.videos},
        )

    def test_duplicate_preserves_order_and_language(self):
        """Order and language_code are copied to new Devotionals."""
        self.admin._duplicate_devotional_set(self.old_ds)
        new_devotionals = Devotional.objects.filter(day__fast=self.target_fast)
        for dev in new_devotionals:
            self.assertEqual(dev.order, 1)
            self.assertEqual(dev.language_code, "en")

    def test_duplicate_does_not_modify_source(self):
        """The source DevotionalSet and its Devotionals are untouched."""
        self.admin._duplicate_devotional_set(self.old_ds)
        self.old_ds.refresh_from_db()
        self.assertEqual(self.old_ds.fast, self.old_fast)
        self.assertEqual(
            Devotional.objects.filter(day__fast=self.old_fast).count(), 5
        )
