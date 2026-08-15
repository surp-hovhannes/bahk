"""Add the date a Feast row can be re-derived from.

A commemoration has no date -- that is the point of the re-key -- but the *name* it carries came
from the engine on some particular day, and recording one lets a later release's rename be
followed from the row itself instead of from a checked-in map.

Schema only.  0065 does the data work in its own transaction: PostgreSQL defers this column's
creation until this migration's schema editor closes, so the updates and deletes there would
otherwise leave pending trigger events behind it -- the same boundary #482 had to introduce for
the re-key.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0063_finalize_feast_rekey'),
    ]

    operations = [
        migrations.AddField(
            model_name='feast',
            name='sample_date',
            field=models.DateField(blank=True, help_text="One date the lectionary engine gave this feast its name, recorded when the row was created. Not the feast's date -- a commemoration recurs and has no single date. It exists so an engine upgrade that renames a feast can be followed: recompute the name for this date and the row can be moved onto it. See remap_feast_names.", null=True),
        ),
    ]
