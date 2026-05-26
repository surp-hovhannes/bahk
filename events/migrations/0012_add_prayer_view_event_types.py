from django.db import migrations


def forwards(apps, schema_editor):
    EventType = apps.get_model("events", "EventType")

    defaults = [
        {
            "code": "prayer_viewed",
            "name": "Prayer Viewed",
            "category": "analytics",
            "requires_target": True,
            "track_in_analytics": True,
            "is_active": True,
        },
        {
            "code": "prayer_request_viewed",
            "name": "Prayer Request Viewed",
            "category": "analytics",
            "requires_target": True,
            "track_in_analytics": True,
            "is_active": True,
        },
    ]

    for row in defaults:
        EventType.objects.update_or_create(code=row["code"], defaults=row)


def backwards(apps, schema_editor):
    # Preserve event history on rollback. Event.event_type cascades on delete.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0011_alter_usermilestone_milestone_type"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
