"""Re-key Feast from (day, name) onto (church, name).

Feast rows hung off Day, so the same commemoration earned a new row -- and a new LLM-generated
context, icon match and designation -- on every recurrence.  The engine emits a few hundred
distinct names across its whole supported range, so the table was overwhelmingly duplicates.

The steps have to run in one migration, in this order, because the intermediate states are not
schemas the app can serve: ``church`` is meaningless until backfilled, and the unique constraint
cannot be added until the duplicates are gone.

Nothing is deleted except redundant Feast rows.  Contexts are reparented onto the survivor and the
ones that lose are marked inactive rather than removed, so the merge can be inspected afterwards
(and largely reversed by hand) if it picked wrong.  Run ``audit_feast_duplicates`` on the pre-
migration database first: it applies the same rule from ``hub.services.feast_merge`` and reports
what this will do.
"""
from django.db import migrations, models
import django.db.models.deletion

from hub.services.feast_merge import survivor


def backfill_church(apps, schema_editor):
    """Copy each feast's church down from its day."""
    Feast = apps.get_model("hub", "Feast")
    for feast in Feast.objects.select_related("day").iterator():
        feast.church_id = feast.day.church_id
        feast.save(update_fields=["church"])


def merge_duplicates(apps, schema_editor):
    """Collapse same-named feasts within a church onto one row.

    Uses ``hub.services.feast_merge.survivor``, the same rule ``audit_feast_duplicates`` reports,
    so the dry run and the migration cannot disagree. It reads only attributes the historical
    model exposes, which is why it lives outside ``hub.models``.
    """
    Feast = apps.get_model("hub", "Feast")
    FeastContext = apps.get_model("hub", "FeastContext")

    groups = {}
    for feast in Feast.objects.all().prefetch_related("contexts"):
        groups.setdefault((feast.church_id, feast.name), []).append(feast)

    for group in groups.values():
        if len(group) == 1:
            continue

        merge = survivor(group)
        keeper = merge["keep"]
        absorbed_ids = [f.id for f in merge["absorbed"]]

        # Reparent every context onto the survivor before its old feast is deleted; the FK
        # cascades, so an unreparented context would be destroyed with its row.
        FeastContext.objects.filter(feast_id__in=absorbed_ids).update(feast_id=keeper.id)

        kept_context = merge["context_kept"]
        if kept_context is not None:
            # Fold the group's whole feedback total onto the surviving context, and retire the
            # rest rather than deleting them.
            FeastContext.objects.filter(pk=kept_context.pk).update(
                active=True,
                thumbs_up=merge["thumbs_up"],
                thumbs_down=merge["thumbs_down"],
            )
            FeastContext.objects.filter(feast_id=keeper.id).exclude(
                pk=kept_context.pk
            ).update(active=False, thumbs_up=0, thumbs_down=0)

        # Carry over enrichment the survivor is missing (it is the oldest row, so it usually has
        # it already; this rescues the case where only a later occurrence was curated).
        update_fields = []
        if not keeper.icon_id and merge["icon_id"]:
            keeper.icon_id = merge["icon_id"]
            update_fields.append("icon")
        if not keeper.designation and merge["designation"]:
            keeper.designation = merge["designation"]
            update_fields.append("designation")
        if update_fields:
            keeper.save(update_fields=update_fields)

        Feast.objects.filter(id__in=absorbed_ids).delete()


def unmerge(apps, schema_editor):
    """Reversing the merge cannot restore the rows it collapsed.

    Deliberately a no-op rather than an error: the schema steps around it reverse cleanly, so
    ``migrate hub 0059`` still restores a working (if de-duplicated) table. The lost rows were
    duplicates of the survivor; what a rollback cannot bring back is which date each one hung on.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0059_reading_text_moves_to_passage_text"),
    ]

    operations = [
        # 1. Add the new key, nullable, so existing rows survive the schema change.
        migrations.AddField(
            model_name="feast",
            name="church",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="feasts",
                to="hub.church",
            ),
        ),
        # 2. Fill it from the day each feast hung on, then collapse the duplicates that were only
        #    ever distinct because of the date.
        migrations.RunPython(backfill_church, migrations.RunPython.noop),
        migrations.RunPython(merge_duplicates, unmerge),
        # 3. Now that every row has a church and the duplicates are gone, the key can be enforced.
        migrations.AlterField(
            model_name="feast",
            name="church",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="feasts",
                to="hub.church",
            ),
        ),
        migrations.AddConstraint(
            model_name="feast",
            constraint=models.UniqueConstraint(
                fields=("church", "name"), name="unique_feast_per_church"
            ),
        ),
        # 4. The date link is what this migration exists to remove.
        migrations.RemoveField(model_name="feast", name="day"),
    ]
