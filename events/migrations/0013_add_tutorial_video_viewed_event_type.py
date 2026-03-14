from django.db import migrations


def forwards(apps, schema_editor):
    EventType = apps.get_model("events", "EventType")

    defaults = {
        "code": "tutorial_video_viewed",
        "name": "Tutorial Video Viewed",
        "category": "analytics",
        "requires_target": True,
        "track_in_analytics": True,
        "is_active": True,
    }

    EventType.objects.update_or_create(code=defaults["code"], defaults=defaults)


def backwards(apps, schema_editor):
    EventType = apps.get_model("events", "EventType")
    EventType.objects.filter(code="tutorial_video_viewed").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0012_add_prayer_view_event_types"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
