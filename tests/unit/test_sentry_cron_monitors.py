"""Tests for Sentry Cron monitor configuration on scheduled Celery tasks."""

from django.test import SimpleTestCase

from bahk.celery import app

# Import modules that register tasks used by app.conf.beat_schedule.
import events.tasks  # noqa: F401
import hub.tasks  # noqa: F401
import notifications.tasks  # noqa: F401
import prayers.tasks  # noqa: F401


class SentryCronMonitorTests(SimpleTestCase):
    """Ensure code-managed cron tasks are configured for Sentry beat monitoring."""

    def test_sentry_beat_tasks_have_monitor_slugs_and_registered_tasks(self):
        missing = []
        for entry_name, entry in app.conf.beat_schedule.items():
            sentry_options = entry.get("options", {}).get("sentry")
            if not sentry_options or not sentry_options.get("monitor_slug"):
                missing.append(f"{entry_name}: missing Sentry monitor_slug")

            task_name = entry["task"]
            task = app.tasks.get(task_name)
            if task is None:
                missing.append(f"{entry_name}: task {task_name} is not registered")

        self.assertEqual(missing, [])

    def test_sentry_beat_tasks_are_not_also_monitor_wrapped(self):
        """Avoid duplicate check-ins from beat metadata plus task decorators."""
        wrapped = []
        for entry_name, entry in app.conf.beat_schedule.items():
            if not entry.get("options", {}).get("sentry"):
                continue

            task_name = entry["task"]
            task = app.tasks.get(task_name)
            if task is not None and getattr(task.run, "__wrapped__", None):
                wrapped.append(f"{entry_name}: {task_name}")

        self.assertEqual(wrapped, [])
