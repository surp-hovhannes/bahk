"""Enforce the observance id once the backfill has committed."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0066_backfill_feast_observance_ids"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="feast",
            constraint=models.UniqueConstraint(
                condition=models.Q(("observance_id__isnull", False)),
                fields=("church", "observance_id"),
                name="unique_feast_observance_id_per_church",
            ),
        ),
    ]
