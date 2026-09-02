"""Move every stored feast name onto the name armenian-lectionary emits now.

Production's names were written by three sources that no longer agree.  The retired
sacredtradition.am scrape stored the site's raw text -- components jammed together where the
source separated them with ``<br>``, HTML entities un-unescaped, the source's Cyrillic homoglyphs
intact.  Engine releases 1.1.x/1.2.x wrote their own names after the scrape was retired.  1.3.0
then departed from the source deliberately: a reviewed ground truth folds ``Saint(s)`` to
``St(s).`` and corrects 122 component spellings, and calendar-position labels are regenerated per
date rather than frozen from one year.

Since the re-key, the name is the lookup key.  So each of those older spellings is a row nothing
will ever find again, holding LLM-generated contexts, a curated icon and an AI designation, while
a date lookup mints an empty row beside it under the current name.  This migration collapses the
two and keeps the enrichment.

The rule is ``hub.services.feast_rename``, shared verbatim with ``manage.py remap_feast_names`` so
the automatic and the manual path cannot drift -- the same arrangement 0062 has with
``feast_merge``.  Rows nothing can resolve are reported and left alone, never deleted;
``audit_feast_duplicates`` lists them afterwards.

Irreversible in substance: once two rows are merged, which enrichment came from which is gone.
"""
from django.db import migrations

from hub.cache import invalidate_feast_api_cache_for_feast
from hub.services.feast_rename import (
    apply_group, describe, engine_names, load_name_map, plan_renames, refresh_metadata,
    stale_metadata,
)


def remap_names(apps, schema_editor):
    Church = apps.get_model("hub", "Church")
    Feast = apps.get_model("hub", "Feast")
    FeastContext = apps.get_model("hub", "FeastContext")

    reachable = engine_names()
    name_map = load_name_map()

    for church in Church.objects.all():
        feasts = list(Feast.objects.filter(church=church).prefetch_related("contexts"))
        if not feasts:
            continue

        groups, _unmapped = plan_renames(feasts, reachable, name_map=name_map)
        touched = False
        for target_name, group in groups:
            if describe(target_name, group) == "unchanged" and not stale_metadata(
                group[0], target_name
            ):
                continue
            keeper = apply_group(target_name, group, Feast, FeastContext)
            refresh_metadata(keeper, target_name)
            keeper.save()
            touched = True

        # Responses cached under the old names would otherwise be served until they expire. One
        # generation bump per church orphans every entry it owns; see hub/cache.py.
        if touched:
            invalidate_feast_api_cache_for_feast(feasts[0])


def unremap(apps, schema_editor):
    """The pre-rename names cannot be reconstructed once merged; reverse is a no-op."""


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0064_feast_sample_date"),
    ]

    operations = [
        migrations.RunPython(remap_names, unremap),
    ]
