"""Tests for Celery beat database synchronization."""

from celery.schedules import crontab
from django.test import TestCase
from django_celery_beat.models import CrontabSchedule, PeriodicTask

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
