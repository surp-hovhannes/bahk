"""Add the observance id, and drop the name key it replaces.

``Feast`` was unique on ``(church, name)``.  That made the display text the identity, and the
display text is the thing armenian-lectionary corrects: 1.3.0 folded ``Saint(s)`` to ``St(s).``
and fixed 122 component spellings, stranding 158 stored names, and 2.0.0 renamed the Sunday
families and retired the bare ``Fast day`` marker on top of that.  Repairing the text each time
was the old answer; this re-keys so there is nothing to repair.

The identity is ONE observance id, not the day's list of them: a row is a commemoration, and a
day that names two commemorations gets two rows.

The name constraint has to go *before* the backfill, not merely alongside it, and not only
because the backfill would trip it mid-flight.  The two keys are not interchangeable: the engine
distinguishes observances English conflates -- ``"Fast day"`` was three distinct ids inside the
supported range, the general ``fast_day`` plus ``illuminator_fast_day_3`` and ``_5``, which the
source heads with their ordinal in Armenian and flattens to ``Fast day`` in English.  Three rows
must be allowed to share that name.

Schema only.  0066 does the data work and 0067 takes the new constraint, each in its own
transaction: PostgreSQL defers this column's index until this migration's schema editor closes,
so the updates and deletes in 0066 would otherwise leave pending trigger events behind it -- the
boundary #482 had to introduce for the first re-key.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0064_fastintention_matched_tags_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="feast",
            name="unique_feast_per_church",
        ),
        migrations.AddField(
            model_name="feast",
            name="observance_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "The identity of this commemoration: one of the engine's published "
                    "observance ids. Stable across engine releases in a way the name is not -- a "
                    "published id keeps meaning the same observance, while the display text gets "
                    "corrected. Null only on rows nothing could resolve."
                ),
                max_length=255,
                null=True,
            ),
        ),
    ]
