"""Enforce the observance key once the backfill has committed."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0067_backfill_feast_observance_keys"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="feast",
            constraint=models.UniqueConstraint(
                condition=models.Q(("observance_key__isnull", False)),
                fields=("church", "observance_key"),
                name="unique_feast_observance_per_church",
            ),
        ),
    ]
