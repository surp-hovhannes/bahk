"""Put every Feast row under the observance the engine says it is.

Each row is resolved to an ``observance_id`` through its stored name -- either a name the engine
still emits, or an old spelling in ``hub/data/feast_name_map.json``, which records the DATE each
retired name resolved to.  A date does not go stale the way the map's target names did (126 of
229 stopped being emitted verbatim at 2.0.0), so asking the current engine what that day
commemorates is what places the row.

Rows that turn out to be the same observance under different spellings are collapsed onto one.
That is not a hypothetical: the name key accumulated a row per spelling.

A row is a COMMEMORATION now, so a legacy row whose day named two of them resolves to the
leading one, which is the component its stored name and its generated enrichment were dominated
by.  The other commemoration is deliberately not invented here: it gets its own row the first
time the ordinary request path serves that date, exactly as any newly seen commemoration does.
This migration therefore never creates rows, only re-keys and merges them.

The rule is ``hub.services.feast_rename``, shared verbatim with ``manage.py remap_feast_names``,
which is the same arrangement 0062 has with ``feast_merge``.  Rows nothing can resolve keep a null
key and are left alone, never deleted; ``audit_feast_duplicates`` lists them afterwards.

Irreversible in substance: once two rows are merged, which enrichment came from which is gone.
"""
from django.db import migrations

from hub.cache import invalidate_feast_api_cache_for_feast
from hub.services.feast_rename import (
    apply_group, describe, engine_names, load_name_map, plan_renames, refresh_metadata,
    stale_metadata,
)


def backfill_observance_ids(apps, schema_editor):
    Church = apps.get_model("hub", "Church")
    Feast = apps.get_model("hub", "Feast")
    FeastContext = apps.get_model("hub", "FeastContext")

    reachable = engine_names()
    name_map = load_name_map()

    for church in Church.objects.all():
        feasts = list(Feast.objects.filter(church=church).prefetch_related("contexts"))
        if not feasts:
            continue

        groups, _unresolved = plan_renames(feasts, reachable, name_map)
        touched = False
        for key, group in groups:
            if describe(key, group) == "unchanged" and not stale_metadata(group[0], key):
                continue
            keeper = apply_group(key, group, Feast, FeastContext)
            refresh_metadata(keeper, key)
            keeper.save()
            touched = True

        # Responses cached under the old rows would otherwise be served until they expire. One
        # generation bump per church orphans every entry it owns; see hub/cache.py.
        if touched:
            invalidate_feast_api_cache_for_feast(feasts[0])


def unbackfill(apps, schema_editor):
    """Merged rows cannot be reconstructed; reverse is a no-op."""


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0065_feast_observance_id"),
    ]

    operations = [
        migrations.RunPython(backfill_observance_ids, unbackfill),
    ]
