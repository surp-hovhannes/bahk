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
            # Handle Readings: check for unique constraint conflicts
            for reading in Reading.objects.filter(day=extra):
                # Check if keeper already has a reading with same (book, chapters, verses)
                if Reading.objects.filter(
                    day=keeper,
                    book=reading.book,
                    start_chapter=reading.start_chapter,
                    start_verse=reading.start_verse,
                    end_chapter=reading.end_chapter,
                    end_verse=reading.end_verse,
                ).exists():
                    # Keeper already has this reading, delete the extra one
                    reading.delete()
                else:
                    # Safe to update
                    reading.day = keeper
                    reading.save()
            
            # Handle Devotionals: check for unique constraint conflicts
            for devotional in Devotional.objects.filter(day=extra):
                # Check if keeper already has a devotional with same (order, language_code)
                if Devotional.objects.filter(
                    day=keeper,
                    order=devotional.order,
                    language_code=devotional.language_code,
                ).exists():
                    # Keeper already has this devotional, delete the extra one
                    devotional.delete()
                else:
                    # Safe to update
                    devotional.day = keeper
                    devotional.save()
            
            # Handle Feasts: only one feast per day, so delete extra if keeper has one
            extra_feast = Feast.objects.filter(day=extra).first()
            if extra_feast:
                if Feast.objects.filter(day=keeper).exists():
                    # Keeper already has a feast, delete the extra one
                    extra_feast.delete()
                else:
                    # Safe to update
                    extra_feast.day = keeper
                    extra_feast.save()
            
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
