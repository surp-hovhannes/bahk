"""Add the observance key, and drop the name key it replaces.

``Feast`` was unique on ``(church, name)``.  That made the display text the identity, and the
display text is the thing armenian-lectionary corrects: 1.3.0 alone folded ``Saint(s)`` to
``St(s).`` and fixed 122 component spellings, which left 158 stored names unreachable.  0065
repaired that; this re-keys so it cannot recur.

The name constraint has to go *before* the backfill, not merely alongside it, and not only
because the backfill would trip it mid-flight.  The two keys are not interchangeable: the engine
distinguishes observances English conflates, and ``"Fast day"`` is three distinct keys inside the
supported range -- the general ``fast_day`` plus ``illuminator_fast_day_3`` and ``_5``, which the
source heads with their ordinal in Armenian and flattens to ``Fast day`` in English.  Three rows
must be allowed to share that name.

Schema only.  0067 does the data work and 0068 takes the new constraint, each in its own
transaction: PostgreSQL defers this column's index until this migration's schema editor closes,
so the updates and deletes in 0067 would otherwise leave pending trigger events behind it -- the
boundary #482 had to introduce for the first re-key.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0065_remap_feast_names_to_engine"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="feast",
            name="unique_feast_per_church",
        ),
        migrations.AddField(
            model_name="feast",
            name="observance_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "The identity of this commemoration: the engine's ordered ObservanceIds for "
                    "the day, joined with '+'. Stable across engine releases in a way the name is "
                    "not -- a published id keeps meaning the same observance, while the display "
                    "text gets corrected. Null only on rows nothing could resolve."
                ),
                max_length=255,
                null=True,
            ),
        ),
    ]
