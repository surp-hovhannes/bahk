"""Tests for the Day deduplication logic in migration 0049.

Verifies that remove_duplicate_days safely handles unique-constraint
collisions on Reading, Feast, and Devotional when merging duplicate
Day records, and that Fast associations are preserved.
"""
import importlib
from datetime import date

from django.apps import apps as django_apps
from django.db import connection
from django.db.models import Count
from django.test import TransactionTestCase

from hub.models import Church, Day, Devotional, Fast, Feast, Reading
from learning_resources.models import Video

_DAY_TABLE_WITHOUT_UNIQUE = '''
    CREATE TABLE "hub_day" (
        "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
        "date" date NOT NULL,
        "fast_id" bigint NULL REFERENCES "hub_fast" ("id") DEFERRABLE INITIALLY DEFERRED,
        "church_id" bigint NOT NULL REFERENCES "hub_church" ("id") DEFERRABLE INITIALLY DEFERRED
    )
'''

_DAY_TABLE_WITH_UNIQUE = '''
    CREATE TABLE "hub_day" (
        "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
        "date" date NOT NULL,
        "fast_id" bigint NULL REFERENCES "hub_fast" ("id") DEFERRABLE INITIALLY DEFERRED,
        "church_id" bigint NOT NULL REFERENCES "hub_church" ("id") DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT "unique_day_per_church" UNIQUE ("date", "church_id")
    )
'''

_DAY_INDEXES = [
    'CREATE INDEX "hub_day_fast_id_17f23ba4" ON "hub_day" ("fast_id")',
    'CREATE INDEX "hub_day_date_e1227f_idx" ON "hub_day" ("date")',
    'CREATE INDEX "hub_day_church_id_89e18882" ON "hub_day" ("church_id")',
    'CREATE INDEX "hub_day_fast_id_c4047f_idx" ON "hub_day" ("fast_id", "date")',
    'CREATE INDEX "hub_day_church__81dcf7_idx" ON "hub_day" ("church_id", "date")',
]


def _load_dedup():
    mod = importlib.import_module("hub.migrations.0049_day_unique_date_church")
    return mod.remove_duplicate_days


def _rebuild_day_table(create_sql):
    """Drop and recreate hub_day (must be empty) with the given CREATE TABLE."""
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute('DROP TABLE IF EXISTS "hub_day"')
        cursor.execute(create_sql)
        for idx in _DAY_INDEXES:
            cursor.execute(idx)
        cursor.execute("PRAGMA foreign_keys = ON")


class RemoveDuplicateDaysTests(TransactionTestCase):
    """Tests for migration 0049's remove_duplicate_days data migration.

    Each test rebuilds hub_day WITHOUT the unique constraint so that
    duplicate rows can be created, then runs the dedup function and
    asserts correct behaviour.  TransactionTestCase flushes all tables
    between tests so hub_day is always empty at setUp time.
    """

    def setUp(self):
        _rebuild_day_table(_DAY_TABLE_WITHOUT_UNIQUE)
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def tearDown(self):
        Day.objects.all().delete()
        _rebuild_day_table(_DAY_TABLE_WITH_UNIQUE)

    def _run_dedup(self):
        _load_dedup()(django_apps, None)

    # ------------------------------------------------------------------ #
    #  Collision scenarios
    # ------------------------------------------------------------------ #

    def test_reading_collision_does_not_crash(self):
        """Duplicate Days with identical Readings must merge without error."""
        day1 = Day.objects.create(date=date(2026, 6, 1), church=self.church)
        day2 = Day.objects.create(date=date(2026, 6, 1), church=self.church)

        Reading.objects.create(
            day=day1, book="Genesis",
            start_chapter=1, start_verse=1, end_chapter=1, end_verse=5,
        )
        Reading.objects.create(
            day=day2, book="Genesis",
            start_chapter=1, start_verse=1, end_chapter=1, end_verse=5,
        )

        self._run_dedup()

        remaining = Day.objects.filter(date=date(2026, 6, 1), church=self.church)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().readings.count(), 1)

    def test_feast_collision_does_not_crash(self):
        """Duplicate Days each with a Feast must merge without error."""
        day1 = Day.objects.create(date=date(2026, 6, 2), church=self.church)
        day2 = Day.objects.create(date=date(2026, 6, 2), church=self.church)

        Feast.objects.create(day=day1, name="Feast of St. James")
        Feast.objects.create(day=day2, name="Feast of St. James")

        self._run_dedup()

        remaining = Day.objects.filter(date=date(2026, 6, 2), church=self.church)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().feasts.count(), 1)

    def test_devotional_collision_does_not_crash(self):
        """Duplicate Days with same (order, language_code) Devotional must merge."""
        day1 = Day.objects.create(date=date(2026, 6, 3), church=self.church)
        day2 = Day.objects.create(date=date(2026, 6, 3), church=self.church)

        video = Video.objects.create(
            title="Test", description="Desc",
            category="devotional", language_code="en",
        )
        Devotional.objects.create(day=day1, video=video, order=1, language_code="en")
        Devotional.objects.create(day=day2, video=video, order=1, language_code="en")

        self._run_dedup()

        remaining = Day.objects.filter(date=date(2026, 6, 3), church=self.church)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().devotionals.count(), 1)

    def test_keeper_adopts_fast_from_extra(self):
        """If the keeper Day has no fast but the extra does, adopt it."""
        fast = Fast.objects.create(
            name="Adopt Fast", church=self.church,
            description="d", culmination_feast="c",
        )
        day1 = Day.objects.create(date=date(2026, 6, 4), church=self.church, fast=None)
        day2 = Day.objects.create(date=date(2026, 6, 4), church=self.church, fast=fast)

        self._run_dedup()

        remaining = Day.objects.get(date=date(2026, 6, 4), church=self.church)
        self.assertEqual(remaining.fast_id, fast.id)

    # ------------------------------------------------------------------ #
    #  Non-colliding data should still be moved
    # ------------------------------------------------------------------ #

    def test_non_colliding_readings_are_moved(self):
        """Unique Readings from extra Day should be moved to keeper."""
        day1 = Day.objects.create(date=date(2026, 6, 5), church=self.church)
        day2 = Day.objects.create(date=date(2026, 6, 5), church=self.church)

        Reading.objects.create(
            day=day1, book="Genesis",
            start_chapter=1, start_verse=1, end_chapter=1, end_verse=5,
        )
        Reading.objects.create(
            day=day2, book="Exodus",
            start_chapter=2, start_verse=1, end_chapter=2, end_verse=10,
        )

        self._run_dedup()

        remaining = Day.objects.get(date=date(2026, 6, 5), church=self.church)
        self.assertEqual(remaining.readings.count(), 2)
        self.assertTrue(remaining.readings.filter(book="Genesis").exists())
        self.assertTrue(remaining.readings.filter(book="Exodus").exists())

    def test_non_colliding_devotionals_are_moved(self):
        """Unique Devotionals from extra Day should be moved to keeper."""
        day1 = Day.objects.create(date=date(2026, 6, 6), church=self.church)
        day2 = Day.objects.create(date=date(2026, 6, 6), church=self.church)

        video = Video.objects.create(
            title="V", description="D",
            category="devotional", language_code="en",
        )
        Devotional.objects.create(day=day1, video=video, order=1, language_code="en")
        Devotional.objects.create(day=day2, video=video, order=2, language_code="en")

        self._run_dedup()

        remaining = Day.objects.get(date=date(2026, 6, 6), church=self.church)
        self.assertEqual(remaining.devotionals.count(), 2)
