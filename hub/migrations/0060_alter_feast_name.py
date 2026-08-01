"""Widen Feast.name from 256 to 512 characters.

WHY
    Two Armenian lectionary feast names enumerate their saints inside the name and exceed
    256 characters -- the Twelve Holy Doctors (289) and the Holy Fathers of Egypt (257).
    Both recur annually, so 54 dates in 2001-2027 are affected. On PostgreSQL the insert
    raises DataError, which makes the API degrade to "no feast" for that day and aborts a
    range import partway through. SQLite (the test DB) does not enforce max_length, which
    is why no test caught it.

    The strings are byte-identical to what sacredtradition.am served, so the retired
    scrape hit exactly the same error: this is a pre-existing defect, not a regression
    from computing feast names offline. Widening rather than truncating keeps the names
    whole -- they are correct, just long.

FORWARD SAFETY
    On PostgreSQL, increasing a varchar's length limit is a catalog-only change: no table
    rewrite, no full-table lock, no data touched. Safe to apply while serving traffic.

ROLLBACK
    The reverse migration is NOT safe once any stored name exceeds 256 characters --
    PostgreSQL refuses to narrow a column whose data would not fit. The documented
    down-path is to truncate first, then reverse:

        UPDATE hub_feast SET name = LEFT(name, 256) WHERE LENGTH(name) > 256;
        python manage.py migrate hub 0059

    That truncation is lossy and irreversible, so capture the affected rows first:

        SELECT id, day_id, name FROM hub_feast WHERE LENGTH(name) > 256;

    Feast.name_hy is unaffected either way: django-modeltrans stores it in the `i18n`
    JSONField, which has no length limit.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0059_reading_text_moves_to_passage_text'),
    ]

    operations = [
        migrations.AlterField(
            model_name='feast',
            name='name',
            field=models.CharField(max_length=512),
        ),
    ]
