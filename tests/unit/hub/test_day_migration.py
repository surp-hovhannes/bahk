"""Tests for Day migration data-copy helpers."""

import importlib
from types import SimpleNamespace
from unittest import TestCase


migration_0012 = importlib.import_module("hub.migrations.0012_fast_m2m_to_fk_mapping")
migration_0056 = importlib.import_module("hub.migrations.0056_backfill_day_church_from_fast")


class RelatedFasts:
    def __init__(self, fast):
        self.fast = fast

    def all(self):
        return self

    def first(self):
        return self.fast


class HistoricalDay:
    def __init__(self, fast, church_id):
        self._fast = None
        self.church_id = church_id
        self.fasts = RelatedFasts(fast)
        self.saved = False

    def save(self):
        self.saved = True


class HistoricalManager:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class HistoricalApps:
    def __init__(self, days):
        self.day_model = SimpleNamespace(objects=HistoricalManager(days))

    def get_model(self, app_label, model_name):
        if (app_label, model_name) == ("hub", "Day"):
            return self.day_model
        raise LookupError(f"Unexpected historical model: {app_label}.{model_name}")


class DayFastMigrationTests(TestCase):
    def test_save_fast_fk_copies_church_from_selected_fast(self):
        fast = SimpleNamespace(church_id=42)
        day = HistoricalDay(fast=fast, church_id=1)

        migration_0012.save_fast_fk(HistoricalApps([day]), schema=None)

        self.assertIs(day._fast, fast)
        self.assertEqual(day.church_id, fast.church_id)
        self.assertTrue(day.saved)

    def test_save_fast_fk_leaves_church_untouched_when_no_fast(self):
        day = HistoricalDay(fast=None, church_id=7)

        migration_0012.save_fast_fk(HistoricalApps([day]), schema=None)

        self.assertIsNone(day._fast)
        self.assertEqual(day.church_id, 7)
        self.assertTrue(day.saved)


class BackfillDayRow:
    def __init__(self, fast, church_id):
        self.fast = fast
        self.church_id = church_id
        self.saved_fields = None

    def save(self, update_fields=None):
        self.saved_fields = update_fields


class BackfillManager:
    """Minimal stub for ``Day.objects`` supporting the migration's query chain.

    The real migration calls ``.filter(fast__isnull=False).select_related("fast")``
    then iterates; only rows with a fast are yielded, mirroring the ORM.
    """

    def __init__(self, rows):
        self._rows = rows

    def filter(self, **kwargs):
        assert kwargs == {"fast__isnull": False}
        return BackfillManager([r for r in self._rows if r.fast is not None])

    def select_related(self, *args):
        return self

    def __iter__(self):
        return iter(self._rows)


class BackfillApps:
    def __init__(self, days):
        self.day_model = SimpleNamespace(objects=BackfillManager(days))

    def get_model(self, app_label, model_name):
        if (app_label, model_name) == ("hub", "Day"):
            return self.day_model
        raise LookupError(f"Unexpected historical model: {app_label}.{model_name}")


class BackfillDayChurchTests(TestCase):
    def test_backfill_updates_only_mismatched_rows(self):
        mismatched = BackfillDayRow(fast=SimpleNamespace(church_id=42), church_id=1)
        already_correct = BackfillDayRow(fast=SimpleNamespace(church_id=5), church_id=5)

        migration_0056.backfill_day_church_from_fast(BackfillApps([mismatched, already_correct]), schema_editor=None)

        self.assertEqual(mismatched.church_id, 42)
        self.assertEqual(mismatched.saved_fields, ["church_id"])
        # Row already matching its fast is left untouched (no write).
        self.assertEqual(already_correct.church_id, 5)
        self.assertIsNone(already_correct.saved_fields)

    def test_backfill_skips_days_without_a_fast(self):
        no_fast = BackfillDayRow(fast=None, church_id=1)

        migration_0056.backfill_day_church_from_fast(BackfillApps([no_fast]), schema_editor=None)

        self.assertEqual(no_fast.church_id, 1)
        self.assertIsNone(no_fast.saved_fields)
