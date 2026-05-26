"""Import smoke tests for the hub task package."""

import importlib

from django.test import SimpleTestCase


class HubTaskImportTests(SimpleTestCase):
    def test_package_exports_and_submodule_imports_coexist(self):
        tasks = importlib.import_module("hub.tasks")

        importlib.import_module("hub.tasks.email_tasks")
        importlib.import_module("hub.tasks.armenian_text_tasks")
        importlib.import_module("hub.tasks.bible_api_tasks")

        self.assertTrue(hasattr(tasks, "send_fast_reminder_task"))
        self.assertTrue(hasattr(tasks, "fetch_armenian_reading_text_task"))
        self.assertTrue(hasattr(tasks, "refresh_all_reading_texts_task"))
