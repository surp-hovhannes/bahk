from django.db import migrations, models


class Migration(migrations.Migration):
    """Update the ``Reading.text_hy_fetched_at`` help text.

    Armenian reading text is now composed from the offline BibleVerse corpus rather than
    fetched live from sacredtradition.am; only the help text changes (no schema change).
    """

    dependencies = [
        ('hub', '0055_sync_day_church_and_fastintention_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reading',
            name='text_hy_fetched_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='When the Armenian text was last composed from the offline Bible corpus',
            ),
        ),
    ]
