from django.db import migrations


def backfill_day_church_from_fast(apps, schema_editor):
    """Repair Day.church for rows mis-assigned the default church.

    Migration 0011 stamped every existing Day with the default church, and the
    original 0012 data copy only populated ``_fast`` without correcting the
    church. Because 0012 has long since been applied everywhere, editing it does
    not re-run on existing databases -- so this forward-only migration backfills
    church_id from each Day's fast for rows that never got the right value.
    """
    Day = apps.get_model("hub", "Day")
    for day in Day.objects.filter(fast__isnull=False).select_related("fast"):
        if day.church_id != day.fast.church_id:
            day.church_id = day.fast.church_id
            day.save(update_fields=["church_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("hub", "0055_sync_day_church_and_fastintention_indexes"),
    ]

    operations = [
        migrations.RunPython(
            backfill_day_church_from_fast,
            migrations.RunPython.noop,
        ),
    ]
