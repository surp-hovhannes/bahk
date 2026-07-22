import logging

from django.db import migrations, models

logger = logging.getLogger(__name__)


def remove_duplicate_days(apps, schema_editor):
    """
    Before adding the unique constraint on (date, church), remove any duplicate
    Day records.  For each duplicate group, keep the Day with the most readings
    (falling back to the lowest id), move FK references to the keeper — skipping
    Readings/Devotionals that would violate their own per-day uniqueness
    constraints — adopt the fast association if the keeper lacks one, then delete
    the extras.

    This step is irreversible (reverse is a no-op) and *deletes* data, so it
    logs every duplicate group and every row it merges or drops at WARNING so
    the effect is auditable in the migrate output / Sentry after a prod run.
    """
    Day = apps.get_model('hub', 'Day')
    Reading = apps.get_model('hub', 'Reading')
    Devotional = apps.get_model('hub', 'Devotional')
    Feast = apps.get_model('hub', 'Feast')

    from django.db.models import Count

    duplicates = list(
        Day.objects.values('date', 'church_id')
        .annotate(cnt=Count('id'))
        .filter(cnt__gt=1)
    )

    if not duplicates:
        logger.info("0055 dedup: no duplicate (date, church) Day groups found.")
        return

    logger.warning(
        "0055 dedup: found %d duplicate (date, church) Day group(s) to merge.",
        len(duplicates),
    )

    for dup in duplicates:
        days = list(
            Day.objects.filter(date=dup['date'], church_id=dup['church_id'])
            .annotate(reading_count=Count('readings'))
            .order_by('-reading_count', 'id')
        )
        keeper = days[0]
        logger.warning(
            "0055 dedup: (date=%s, church_id=%s) keeping Day id=%s (fast_id=%s), "
            "merging %d extra(s): %s",
            dup['date'], dup['church_id'], keeper.pk, keeper.fast_id,
            len(days) - 1, [d.pk for d in days[1:]],
        )
        for extra in days[1:]:
            # Adopt fast if keeper doesn't have one
            if not keeper.fast_id and extra.fast_id:
                logger.warning(
                    "0055 dedup: Day id=%s adopting fast_id=%s from extra Day id=%s",
                    keeper.pk, extra.fast_id, extra.pk,
                )
                Day.objects.filter(pk=keeper.pk).update(fast_id=extra.fast_id)
                keeper.fast_id = extra.fast_id
            elif keeper.fast_id and extra.fast_id and keeper.fast_id != extra.fast_id:
                logger.warning(
                    "0055 dedup: DROPPING fast association fast_id=%s from extra "
                    "Day id=%s (keeper Day id=%s already has fast_id=%s)",
                    extra.fast_id, extra.pk, keeper.pk, keeper.fast_id,
                )

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

            # Move Feasts to the keeper. unique_feast_per_day was removed in
            # migration 0052, so a Day may legitimately hold multiple feasts —
            # reassign them all rather than dropping any (no data loss).
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
        ('hub', '0055_sync_day_church_and_fastintention_indexes'),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_days, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='day',
            constraint=models.UniqueConstraint(fields=['date', 'church'], name='unique_day_per_church'),
        ),
    ]
