"""Audit the feast table against the names the engine actually emits.

Before the re-key this command was a dry run of the merge that collapsed ``Feast`` from
``(day, name)`` onto ``(church, name)``: it reported how far the table would shrink and which
contexts the merge would retire.  That reporting is gone because the schema now makes it
impossible -- a unique constraint on ``(church, name)`` means there is no such thing as a
duplicate row to collapse.  The merge rule itself lives on in ``hub.services.feast_merge``, which
the migration applies.

What survives is the half that stays useful indefinitely.  Feast names come from the engine and
are recomputed per request, so a stored name the engine no longer emits is unreachable: its
designation, icon and generated contexts are still in the database but no date lookup will ever
find them again.  That happens whenever an engine upgrade corrects a name -- exactly the sort of
change armenian-lectionary ships regularly -- and it is silent, because nothing errors.

Since the re-key onto ``observance_id`` that is mostly historical: a row identified by a stable
id survives a text correction, and a stale name on it is a display defect rather than a lost row.
What this reports is every way a row can still fall out of that arrangement:

  * **Unreachable name** -- a stored name the engine no longer emits.  Cosmetic on a keyed row,
    the original failure on an unkeyed one.
  * **Pending refresh** -- a row whose id the engine now names differently, i.e. one an upgrade
    has already moved but nothing has written yet.
  * **No id** -- identified by display text alone, which is the arrangement the re-key ended.
  * **Retired id** -- an ``observance_id`` this engine version does not serve.  Should be
    impossible: a published id is a contract.  If this is ever non-empty the engine broke it.

Nothing is fixed here.  ``manage.py remap_feast_names`` is the repair.

Read-only.  ``--church`` scopes it; ``--verbose`` lists every stored name, not just the orphans.
"""
from django.core.management.base import BaseCommand, CommandError

from armenian_lectionary import MAX_YEAR, MIN_YEAR

from hub.models import Church
from hub.services.feast_rename import (
    engine_names, name_for_observance_id, observance_ids,
)


class Command(BaseCommand):
    help = (
        "Read-only: report stored feast names the lectionary engine no longer emits, whose "
        "enrichment is therefore unreachable by a date lookup, and names an upgrade is about "
        "to strand. Run remap_feast_names to repair either."
    )

    def add_arguments(self, parser):
        parser.add_argument("--church", default=None,
                            help="Limit the report to one church, by name (default: all).")
        parser.add_argument("--verbose", action="store_true",
                            help="List every stored name, not just the unreachable ones.")

    def handle(self, *args, **options):
        churches = Church.objects.all()
        if options["church"]:
            churches = churches.filter(name=options["church"])
            if not churches.exists():
                raise CommandError(f"No church named {options['church']!r}.")

        self.stdout.write(
            f"Enumerating engine names for {MIN_YEAR}-{MAX_YEAR}..."
        )
        reachable = engine_names()
        self.stdout.write(f"  engine emits {len(reachable)} distinct names.\n")

        for church in churches:
            self._report_church(church, reachable, options["verbose"])

    def _report_church(self, church, reachable, verbose):
        feasts = list(church.feasts.prefetch_related("contexts"))
        if not feasts:
            self.stdout.write(f"\n{church.name}: no feasts.")
            return

        by_name = {feast.name: feast for feast in feasts}
        unreachable = sorted(set(by_name) - reachable)

        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{church.name}"))
        self.stdout.write(f"  {len(feasts)} commemorations stored")

        if unreachable:
            self.stdout.write(self.style.WARNING(
                f"  {len(unreachable)} stored name(s) the engine no longer emits. On a row that "
                f"has an observance_id this is stale display text, not a lost row -- "
                f"remap_feast_names refreshes it. On one without, it is the old failure: nothing "
                f"reaches the row and its enrichment is stranded:"
            ))
            for name in unreachable:
                feast = by_name[name]
                # Say what is at stake, so the reader can judge how urgently to act.
                held = []
                if not feast.observance_id:
                    held.append("NO ID")
                if feast.designation:
                    held.append(f"designation={feast.designation!r}")
                if feast.icon_id:
                    held.append(f"icon=#{feast.icon_id}")
                contexts = list(feast.contexts.all())
                if contexts:
                    held.append(f"{len(contexts)} context(s)")
                self.stdout.write(
                    f"      #{feast.id} {name!r}"
                    + (f" -- {', '.join(held)}" if held else " -- no enrichment")
                )
        else:
            self.stdout.write("  every stored name is one the engine still emits.")

        # The same failure caught before it lands. A keyed row whose id the engine now names
        # something else is serving stale display text; the next request that resolves it corrects
        # it, but a row nothing requests stays stale until remap_feast_names sweeps it.
        pending = [
            (feast, name_for_observance_id(feast.observance_id))
            for feast in feasts
            if feast.observance_id
        ]
        pending = [(f, current) for f, current in pending if current and current != f.name]
        if pending:
            self.stdout.write(self.style.WARNING(
                f"  {len(pending)} name(s) the engine has already renamed under their own id; "
                f"remap_feast_names moves them:"
            ))
            for feast, current in sorted(pending, key=lambda pair: pair[0].name):
                self.stdout.write(f"      #{feast.id} {feast.name!r}")
                self.stdout.write(f"          -> {current!r} (from {feast.observance_id})")

        # A row with no id is identified only by its display text, which is the arrangement the
        # re-key exists to end -- the next correction to that text strands it.
        unkeyed = [f for f in feasts if not f.observance_id]
        if unkeyed:
            self.stdout.write(self.style.WARNING(
                f"  {len(unkeyed)} row(s) carry no observance_id, so they are identified only by "
                f"display text; remap_feast_names resolves what it can:"))
            for feast in sorted(unkeyed, key=lambda f: f.name)[:20]:
                self.stdout.write(f"      #{feast.id} {feast.name!r}")

        serving = observance_ids()
        retired = [f for f in feasts if f.observance_id and f.observance_id not in serving]
        if retired:
            self.stdout.write(self.style.WARNING(
                f"  {len(retired)} row(s) hold an observance_id this engine no longer serves:"))
            for feast in sorted(retired, key=lambda f: f.observance_id):
                self.stdout.write(f"      #{feast.id} {feast.observance_id!r} ({feast.name!r})")

        if verbose:
            self.stdout.write("\n  all stored names:")
            for name in sorted(by_name):
                mark = " " if name in reachable else "!"
                self.stdout.write(f"    {mark} {name!r}")
