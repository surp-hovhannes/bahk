"""Clear the ``Fast`` designation off rows the engine calls commemorations.

``determine_feast_designation_task`` used to short-circuit names shaped like "<Ordinal> day of
Great Lent" straight to ``FAST`` without asking the classifier, carving out saint words with the
alternation ``Saint|Martyr|Blessed|Holy\\s+(?!Cross)|Prophet|Apostle|Patriarch|Vartapet``.  That
list spells out ``Saint`` and never ``St.``, and the engine's display text overwhelmingly uses the
abbreviation -- so days that plainly name a saint were stamped as generic fasts anyway.

Production carries the damage.  Sweeping the live API across 2026 turns up 22 rows designated
``Fast``, none with any context text, and among them:

    Sixth day of Great Lent - St. Theodore the Tyron          -> theodore_the_tyron
    Thirty Fourth day of Great Lent - St. Gregory ...the Pit  -> gregory_the_illuminator_descent
    Great Thursday - Fast day - Remembrance of the Last Supper-> last_supper
    Great Saturday - Fast day - Eve of the Resurrection...    -> eve_of_the_resurrection
    Fast day - Feast of the Holy Church                       -> feast_of_the_holy_church

every one of which the catalog marks ``is_comm: true, is_fast: false``.

Left alone these never recover, and the re-key does not rescue them: 0066 resolves a joined
legacy name to its LEADING commemoration and ``feast_merge.survivor`` carries ``designation``
onto the survivor, so the stamp rides onto the observance-keyed row.  ``designation`` is never
overwritten once set -- the task returns early -- and it is the only thing standing between a
feast and its generated context.  Great Thursday and Great Saturday would keep a blank card
forever.

So this clears the field rather than guessing a better value.  ``NULL`` is the ordinary
unclassified state (113 rows on production sit there), it makes the row eligible for context
again, and the classifier can fill it in later.

Deliberately narrow.  Only ``is_comm and not is_fast`` is touched: the two marks are independent
and six ids carry both -- the named Lenten Sundays, and Mijink -- where ``Fast`` may well be a
considered answer rather than a regex artifact.  Those keep whatever they have.  A row with no
observance id, or an id this engine does not know, is left alone: silence is not evidence.

Irreversible in substance -- which rows were cleared is not recorded, and the values were wrong.
"""
from django.db import migrations

from hub.services.feast_service import is_commemoration_and_not_a_fast


def clear_fast_designation_on_commemorations(apps, schema_editor):
    Feast = apps.get_model("hub", "Feast")

    # Feast.Designation.FAST is not reachable from the historical model, and its value is the
    # literal below; the choices are declared with value == label.
    repaired = [
        feast.pk
        for feast in Feast.objects.filter(designation="Fast").exclude(observance_id__isnull=True)
        if is_commemoration_and_not_a_fast(feast.observance_id)
    ]
    Feast.objects.filter(pk__in=repaired).update(designation=None)


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0067_finalize_feast_observance_id"),
    ]

    operations = [
        migrations.RunPython(
            clear_fast_designation_on_commemorations,
            migrations.RunPython.noop,
        ),
    ]
