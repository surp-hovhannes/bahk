"""Enforce the commemoration key after the data merge has committed."""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0062_merge_feasts_by_commemoration"),
    ]

    operations = [
        migrations.AlterField(
            model_name="feast",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="feasts",
                to="hub.church",
            ),
        ),
        migrations.AddConstraint(
            model_name="feast",
            constraint=models.UniqueConstraint(
                fields=("church", "name"), name="unique_feast_per_church"
            ),
        ),
        migrations.RemoveField(model_name="feast", name="day"),
    ]
