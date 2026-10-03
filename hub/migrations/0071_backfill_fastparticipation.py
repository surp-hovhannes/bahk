"""Reconcile existing membership/event history once after creating its final schema."""

from django.db import migrations

from hub.services.fast_participation_backfill import backfill_fast_participation


class Migration(migrations.Migration):
    dependencies = [
        ("hub", "0070_fastparticipation"),
        ("events", "0014_drop_duplicate_analytics_indexes"),
    ]

    operations = [
        migrations.RunPython(
            backfill_fast_participation,
            migrations.RunPython.noop,
        ),
    ]
