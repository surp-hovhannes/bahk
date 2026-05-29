# Generated migration to remove day-only unique constraint on Feast

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('hub', '0040_add_feast_models_and_llmprompt_applies_to'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='feast',
            name='unique_feast_per_day',
        ),
    ]
