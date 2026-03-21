from django.db import migrations, models


def remove_duplicate_days(apps, schema_editor):
    """
    Before adding the unique constraint on (date, church), remove any duplicate
    Day records. For each duplicate group, keep the Day with the most readings
    (falling back to the lowest id), move any other FK references to the keeper,
    then delete the rest.
    """
    Day = apps.get_model('hub', 'Day')
    Reading = apps.get_model('hub', 'Reading')
    Devotional = apps.get_model('hub', 'Devotional')
    Feast = apps.get_model('hub', 'Feast')

    from django.db.models import Count

    # Find all (date, church_id) pairs that have more than one Day
    duplicates = (
        Day.objects.values('date', 'church_id')
        .annotate(cnt=Count('id'))
        .filter(cnt__gt=1)
    )

    for dup in duplicates:
        days = list(
            Day.objects.filter(date=dup['date'], church_id=dup['church_id'])
            .annotate(reading_count=Count('readings'))
            .order_by('-reading_count', 'id')
        )
        keeper = days[0]
        for extra in days[1:]:
            Reading.objects.filter(day=extra).update(day=keeper)
            Devotional.objects.filter(day=extra).update(day=keeper)
            Feast.objects.filter(day=extra).update(day=keeper)
            extra.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0048_add_start_day_number_to_fast'),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_days, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='day',
            constraint=models.UniqueConstraint(fields=['date', 'church'], name='unique_day_per_church'),
        ),
    ]
