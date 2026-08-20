"""Backfill Feast.church and merge duplicate commemorations.

The nullable column and its foreign-key index were committed by 0061.  Migration 0063 finalizes
the schema only after the trigger-producing updates and deletes below have committed.

Nothing is deleted except redundant Feast rows. Contexts are reparented onto the survivor and the
ones that lose are marked inactive rather than removed, so the merge can be inspected afterwards.
"""
from django.db import migrations

from hub.services.feast_merge import survivor


def backfill_church(apps, schema_editor):
    """Copy each feast's church down from its day."""
    Feast = apps.get_model("hub", "Feast")
    for feast in Feast.objects.select_related("day").iterator():
        feast.church_id = feast.day.church_id
        feast.save(update_fields=["church"])


def merge_duplicates(apps, schema_editor):
    """Collapse same-named feasts within a church onto one row."""
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

        # Reparent every context before deleting its old feast; the FK cascades.
        FeastContext.objects.filter(feast_id__in=absorbed_ids).update(feast_id=keeper.id)

        kept_context = merge["context_kept"]
        if kept_context is not None:
            FeastContext.objects.filter(pk=kept_context.pk).update(
                active=True,
                thumbs_up=merge["thumbs_up"],
                thumbs_down=merge["thumbs_down"],
            )
            FeastContext.objects.filter(feast_id=keeper.id).exclude(
                pk=kept_context.pk
            ).update(active=False, thumbs_up=0, thumbs_down=0)

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
    """The absorbed rows' original dates cannot be reconstructed; reverse is a no-op."""


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0061_rekey_feast_to_commemoration"),
    ]

    operations = [
        migrations.RunPython(backfill_church, migrations.RunPython.noop),
        migrations.RunPython(merge_duplicates, unmerge),
    ]
