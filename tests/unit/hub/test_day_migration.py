"""Tests for Day migration data-copy helpers."""

import importlib
from types import SimpleNamespace
from unittest import TestCase


migration_0012 = importlib.import_module("hub.migrations.0012_fast_m2m_to_fk_mapping")


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
