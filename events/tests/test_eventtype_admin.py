from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from events.admin import EventTypeAdmin
from events.models import Event, EventType


User = get_user_model()


class EventTypeAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="adminpass123",
            is_staff=True,
            is_superuser=True,
        )

    def test_get_queryset_annotates_events_total(self):
        event_type = EventType.objects.create(code="x", name="X")
        Event.objects.create(event_type=event_type, user=self.admin_user, title="e1")
        Event.objects.create(event_type=event_type, user=self.admin_user, title="e2")

        ma = EventTypeAdmin(EventType, AdminSite())
        qs = ma.get_queryset(request=None)
        obj = qs.get(id=event_type.id)

        self.assertTrue(hasattr(obj, "events_total"))
        self.assertEqual(obj.events_total, 2)

    def test_event_count_uses_annotation_without_extra_queries(self):
        event_type = EventType.objects.create(code="y", name="Y")
        Event.objects.create(event_type=event_type, user=self.admin_user, title="e1")

        ma = EventTypeAdmin(EventType, AdminSite())
        obj = ma.get_queryset(request=None).get(id=event_type.id)

        with CaptureQueriesContext(connection) as ctx:
            html = ma.event_count(obj)

        self.assertEqual(len(ctx), 0)
        self.assertIn("1 events", str(html))


