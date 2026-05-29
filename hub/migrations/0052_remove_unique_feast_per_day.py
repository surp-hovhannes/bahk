# Generated migration to remove day-only unique constraint on Feast

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('hub', '0051_fast_intention'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='feast',
            name='unique_feast_per_day',
        ),
    ]
