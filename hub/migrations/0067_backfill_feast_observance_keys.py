"""Put every Feast row under the observance the engine says it is.

Each row is resolved to an ``observance_key`` -- from its ``sample_date`` where 0065 left one, and
otherwise through its name and the legacy map -- and rows that turn out to be the same observance
under different spellings are collapsed onto one.  That is not a hypothetical: the name key
accumulated a row per spelling, and 0065 could only merge the ones whose *current* names matched.

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


def backfill_observance_keys(apps, schema_editor):
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
        ("hub", "0066_feast_observance_key"),
    ]

    operations = [
        migrations.RunPython(backfill_observance_keys, unbackfill),
    ]
