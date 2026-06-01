"""Tests for Sentry Cron monitor instrumentation on scheduled Celery tasks."""

from django.test import SimpleTestCase

from bahk.celery import app

# Import modules that register tasks used by app.conf.beat_schedule.
import events.tasks  # noqa: F401
import hub.tasks  # noqa: F401
import notifications.tasks  # noqa: F401
import prayers.tasks  # noqa: F401


class SentryCronMonitorTests(SimpleTestCase):
    """Ensure code-managed cron tasks emit Sentry check-ins."""

    def test_sentry_beat_tasks_are_monitor_wrapped(self):
        missing = []
        for entry_name, entry in app.conf.beat_schedule.items():
            sentry_options = entry.get("options", {}).get("sentry")
            if not sentry_options:
                continue

            task_name = entry["task"]
            task = app.tasks.get(task_name)
            if task is None:
                missing.append(f"{entry_name}: task {task_name} is not registered")
                continue

            if not getattr(task.run, "__wrapped__", None):
                monitor_slug = sentry_options.get("monitor_slug")
                missing.append(
                    f"{entry_name}: {task_name} is not wrapped by "
                    f"sentry_sdk.monitor({monitor_slug!r})"
                )

        self.assertEqual(missing, [])
