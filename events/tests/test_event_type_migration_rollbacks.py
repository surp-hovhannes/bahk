import importlib

from django.apps import apps
from django.test import TestCase

from events.models import Event, EventType

migration_0004 = importlib.import_module(
    "events.migrations.0004_create_user_account_created_event_type_record"
)
migration_0012 = importlib.import_module(
    "events.migrations.0012_add_prayer_view_event_types"
)
migration_0013 = importlib.import_module(
    "events.migrations.0013_add_tutorial_video_viewed_event_type"
)


class EventTypeMigrationRollbackTests(TestCase):
    def test_user_account_created_rollback_preserves_events(self):
        event_type, _ = EventType.objects.get_or_create(
            code="user_account_created",
            defaults={"name": "User Account Created", "category": "user_action"},
        )
        event = Event.objects.create(
            event_type=event_type,
            title="User account created",
        )

        migration_0004.reverse_create_user_account_created_event_type(apps, None)

        self.assertTrue(EventType.objects.filter(pk=event_type.pk).exists())
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_prayer_view_rollback_preserves_events(self):
        event_type, _ = EventType.objects.get_or_create(
            code="prayer_viewed",
            defaults={"name": "Prayer Viewed", "category": "analytics"},
        )
        event = Event.objects.create(
            event_type=event_type,
            title="Prayer viewed",
        )

        migration_0012.backwards(apps, None)

        self.assertTrue(EventType.objects.filter(pk=event_type.pk).exists())
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_tutorial_video_rollback_preserves_events(self):
        event_type, _ = EventType.objects.get_or_create(
            code="tutorial_video_viewed",
            defaults={"name": "Tutorial Video Viewed", "category": "analytics"},
        )
        event = Event.objects.create(
            event_type=event_type,
            title="Tutorial video viewed",
        )

        migration_0013.backwards(apps, None)

        self.assertTrue(EventType.objects.filter(pk=event_type.pk).exists())
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())
