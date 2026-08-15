"""Add the nullable church key used to re-key Feast.

Feast rows hung off Day, so the same commemoration earned a new row -- and a new LLM-generated
context, icon match and designation -- on every recurrence.  The engine emits a few hundred
distinct names across its whole supported range, so the table was overwhelmingly duplicates.

PostgreSQL defers creation of the new foreign-key index until this migration's schema editor
closes.  The backfill and merge therefore live in 0062, after this schema transaction commits;
otherwise their trigger events prevent the deferred index from being created.  Migration 0063
then enforces the final constraint and removes ``day`` in a third transaction.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        # 0060 widened Feast.name to 512 (#471); this depends on it so the two stay one chain
        # rather than two leaves off 0059.
        ("hub", "0060_alter_feast_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="feast",
            name="church",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="feasts",
                to="hub.church",
            ),
        ),
    ]
