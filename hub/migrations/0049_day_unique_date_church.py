from django.db import migrations, models


def remove_duplicate_days(apps, schema_editor):
    """
    Before adding the unique constraint on (date, church), remove any duplicate
    Day records.  For each duplicate group, keep the Day with the most readings
    (falling back to the lowest id), move non-colliding FK references to the
    keeper, discard colliding ones, adopt the fast association if the keeper
    lacks one, then delete the extras.
    """
    Day = apps.get_model('hub', 'Day')
    Reading = apps.get_model('hub', 'Reading')
    Devotional = apps.get_model('hub', 'Devotional')
    Feast = apps.get_model('hub', 'Feast')

    from django.db.models import Count

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
            # Adopt fast if keeper doesn't have one
            if not keeper.fast_id and extra.fast_id:
                Day.objects.filter(pk=keeper.pk).update(fast_id=extra.fast_id)
                keeper.fast_id = extra.fast_id

            # Move Readings, skipping those that would violate unique_reading_per_day
            keeper_readings = set(
                Reading.objects.filter(day=keeper).values_list(
                    'book', 'start_chapter', 'start_verse',
                    'end_chapter', 'end_verse',
                )
            )
            for reading in Reading.objects.filter(day=extra):
                key = (
                    reading.book, reading.start_chapter, reading.start_verse,
                    reading.end_chapter, reading.end_verse,
                )
                if key in keeper_readings:
                    reading.delete()
                else:
                    Reading.objects.filter(pk=reading.pk).update(day=keeper)

            # Move Feasts, respecting unique_feast_per_day (one feast per day)
            if Feast.objects.filter(day=keeper).exists():
                Feast.objects.filter(day=extra).delete()
            else:
                Feast.objects.filter(day=extra).update(day=keeper)

            # Move Devotionals, skipping (day, order, language_code) collisions
            keeper_devotionals = set(
                Devotional.objects.filter(day=keeper).values_list(
                    'order', 'language_code',
                )
            )
            for devotional in Devotional.objects.filter(day=extra):
                key = (devotional.order, devotional.language_code)
                if key in keeper_devotionals:
                    devotional.delete()
                else:
                    Devotional.objects.filter(pk=devotional.pk).update(day=keeper)

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
