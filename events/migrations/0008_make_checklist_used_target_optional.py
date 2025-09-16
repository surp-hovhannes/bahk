# Generated for PR #229 analytics enhancements

from django.db import migrations


def update_checklist_used_event_type(apps, schema_editor):
    """Update CHECKLIST_USED event type to not require target."""
    EventType = apps.get_model('events', 'EventType')
    
    try:
        checklist_event_type = EventType.objects.get(code='checklist_used')
        checklist_event_type.requires_target = False
        checklist_event_type.save()
        print("✅ Updated CHECKLIST_USED event type to not require target")
    except EventType.DoesNotExist:
        # Event type doesn't exist yet, will be created correctly by init_event_types
        print("ℹ️ CHECKLIST_USED event type doesn't exist yet - will be created correctly")
        pass


def reverse_update_checklist_used_event_type(apps, schema_editor):
    """Reverse the update to CHECKLIST_USED event type."""
    EventType = apps.get_model('events', 'EventType')
    
    try:
        checklist_event_type = EventType.objects.get(code='checklist_used')
        checklist_event_type.requires_target = True
        checklist_event_type.save()
        print("↩️ Reverted CHECKLIST_USED event type to require target")
    except EventType.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0007_alter_useractivityfeed_activity_type_announcement'),
    ]

    operations = [
        migrations.RunPython(
            update_checklist_used_event_type,
            reverse_update_checklist_used_event_type,
        ),
    ]
