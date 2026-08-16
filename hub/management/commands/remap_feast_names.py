"""Put every stored feast under the observance the lectionary engine says it is.

``Feast`` is keyed by ``(church, observance_key)`` -- the engine's ordered ``ObservanceIds`` for
the day.  A published id keeps meaning the same observance, so unlike the name it does not move
when the engine corrects its display text, and this command's job is mostly done: it exists to
bring rows written before ids forward, to refresh the display text an engine release changed, and
to collapse the duplicate spellings the name key accumulated.

Dry run by default.  Read the report first -- ``merge`` is the line worth reading closely, since
it is the only outcome that deletes a row.

    python manage.py remap_feast_names                 # report, write nothing
    python manage.py remap_feast_names --apply
    python manage.py remap_feast_names --church "..." --apply

Idempotent: a second run reports every row unchanged.  Safe to run on any database, including one
that has never held a stale name.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hub.cache import invalidate_feast_api_cache_for_feast
from hub.models import Church, Feast, FeastContext
from hub.services.feast_rename import (
    apply_group, describe, engine_names, load_name_map, observance_keys, plan_renames,
    refresh_metadata, stale_metadata,
)


class Command(BaseCommand):
    help = (
        "Put stored feasts under the observance the lectionary engine says they are, merging "
        "enrichment where several collapse onto one. Dry run unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the changes. Without it nothing is modified.")
        parser.add_argument("--church", default=None,
                            help="Limit to one church, by name (default: all).")

    def handle(self, *args, **options):
        churches = Church.objects.all()
        if options["church"]:
            churches = churches.filter(name=options["church"])
            if not churches.exists():
                raise CommandError(f"No church named {options['church']!r}.")

        reachable = engine_names()
        name_map = load_name_map()
        self.stdout.write(
            f"engine emits {len(observance_keys())} distinct observances "
            f"({len(reachable)} distinct names); "
            f"{len(name_map)} historical name(s) in the map."
        )
        if not options["apply"]:
            self.stdout.write(self.style.NOTICE("DRY RUN -- nothing will be written.\n"))

        totals = {"unchanged": 0, "rekey": 0, "merge": 0, "unresolved": 0, "absorbed": 0,
                  "refreshed": 0}
        for church in churches:
            self._remap_church(church, reachable, name_map, options["apply"], totals)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{totals['rekey']} re-keyed, {totals['merge']} merged "
            f"({totals['absorbed']} row(s) absorbed), {totals['refreshed']} refreshed in place, "
            f"{totals['unchanged']} already current, {totals['unresolved']} unresolved."
        ))
        pending = totals["rekey"] + totals["merge"] + totals["refreshed"]
        if not options["apply"] and pending:
            self.stdout.write("Re-run with --apply to write these changes.")

    def _remap_church(self, church, reachable, name_map, apply, totals):
        feasts = list(church.feasts.prefetch_related("contexts"))
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{church.name}"))
        if not feasts:
            self.stdout.write("  no feasts.")
            return

        groups, unresolved = plan_renames(feasts, reachable, name_map)

        touched = False
        for key, group in groups:
            action = describe(key, group)
            stale = stale_metadata(group[0], key)
            if action == "unchanged" and stale:
                totals["refreshed"] += 1
                self.stdout.write(f"  refresh   #{group[0].id} {key!r} "
                                  f"-- {', '.join(stale)}")
            else:
                totals[action] += 1
            if action != "unchanged":
                totals["absorbed"] += len(group) - 1
                self._report_group(action, key, group)
            if action == "unchanged" and not stale:
                continue
            touched = True
            if apply:
                self._write_group(key, group)

        for feast in unresolved:
            totals["unresolved"] += 1
            self.stdout.write(self.style.WARNING(
                f"  unresolved #{feast.id} {feast.name!r} -- left as it is; "
                f"{self._enrichment(feast)}"
            ))

        # One generation bump per church clears every feast API entry it owns; the per-row
        # alternative would be enumerating thousands of dates. See hub/cache.py.
        if apply and touched:
            invalidate_feast_api_cache_for_feast(feasts[0])

    def _report_group(self, action, key, group):
        if action == "rekey":
            self.stdout.write(f"  rekey     #{group[0].id} {group[0].name!r}")
            self.stdout.write(f"              -> {key!r}")
            return

        keeper, absorbed = group[0], group[1:]
        self.stdout.write(self.style.WARNING(f"  merge     -> {key!r}"))
        self.stdout.write(f"              keep    #{keeper.id} {keeper.name!r} "
                          f"-- {self._enrichment(keeper)}")
        for feast in absorbed:
            self.stdout.write(f"              absorb  #{feast.id} {feast.name!r} "
                              f"-- {self._enrichment(feast)}")

    @transaction.atomic
    def _write_group(self, key, group):
        """Apply one group, then bring its Armenian name and recorded date up to date.

        ``apply_group`` leaves the survivor unsaved so the row is written once, with the new key
        and the refreshed names and date in the same statement.
        """
        keeper = apply_group(key, group, Feast, FeastContext)
        refresh_metadata(keeper, key)
        keeper.save()

    @staticmethod
    def _enrichment(feast):
        """What this row is holding, so the reader can weigh a merge."""
        held = []
        if feast.designation:
            held.append(f"designation={feast.designation!r}")
        if feast.icon_id:
            held.append(f"icon=#{feast.icon_id}")
        contexts = list(feast.contexts.all())
        if contexts:
            held.append(f"{len(contexts)} context(s)")
        return ", ".join(held) if held else "no enrichment"
