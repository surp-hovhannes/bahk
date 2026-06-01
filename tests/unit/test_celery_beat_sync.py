"""Tests for Celery beat database synchronization."""

from celery.schedules import crontab
from django.test import TestCase
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from django_celery_beat.schedulers import ModelEntry
from sentry_sdk.integrations.celery.beat import _apply_crons_data_to_schedule_entry
from types import SimpleNamespace
from unittest.mock import patch

from bahk.celery import app, sync_beat_schedule_to_db


class BeatScheduleSyncTests(TestCase):
    """Test syncing inline beat_schedule entries into django-celery-beat."""

    def setUp(self):
        self.original_schedule = app.conf.beat_schedule

    def tearDown(self):
        app.conf.beat_schedule = self.original_schedule

    def test_existing_disabled_task_stays_disabled(self):
        """Manual admin disables should survive code schedule sync."""
        name = "send-fast-notifications-every-day"
        old_schedule = CrontabSchedule.objects.create(
            minute="0",
            hour="0",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="America/Los_Angeles",
        )
        task = PeriodicTask.objects.create(
            name=name,
            task="old.task.path",
            crontab=old_schedule,
            enabled=False,
            kwargs="{}",
            description="[code-managed]",
        )

        app.conf.beat_schedule = {
            name: {
                "task": "hub.tasks.send_fast_reminder_task",
                "schedule": crontab(hour=6, minute=30),
            }
        }

        sync_beat_schedule_to_db()

        task.refresh_from_db()
        self.assertFalse(task.enabled)
        self.assertEqual(task.task, "hub.tasks.send_fast_reminder_task")
        self.assertEqual(task.crontab.hour, "6")
        self.assertEqual(task.crontab.minute, "30")

    def test_database_scheduler_entries_receive_sentry_cron_headers(self):
        """DB-backed beat entries still get Sentry cron headers at dispatch."""
        name = "send-fast-notifications-every-day"
        app.conf.beat_schedule = {
            name: {
                "task": "hub.tasks.send_fast_reminder_task",
                "schedule": crontab(hour=6, minute=30),
                "options": {
                    "sentry": {
                        "monitor_slug": "daily-fast-notifications",
                    }
                },
            }
        }

        sync_beat_schedule_to_db()

        periodic_task = PeriodicTask.objects.get(name=name)
        entry = ModelEntry(periodic_task, app=app)
        scheduler = SimpleNamespace(app=app)
        integration = SimpleNamespace(
            monitor_beat_tasks=True,
            exclude_beat_tasks=[],
        )

        with patch(
            "sentry_sdk.integrations.celery.beat.capture_checkin",
            return_value="check-in-id",
        ) as capture_checkin:
            _apply_crons_data_to_schedule_entry(scheduler, entry, integration)

        headers = entry.options["headers"]
        self.assertEqual(headers["sentry-monitor-slug"], name)
        self.assertEqual(headers["sentry-monitor-check-in-id"], "check-in-id")
        self.assertEqual(headers["sentry-monitor-config"]["schedule"]["type"], "crontab")
        self.assertEqual(
            headers["sentry-monitor-config"]["schedule"]["value"],
            "30 6 * * *",
        )
        capture_checkin.assert_called_once()
