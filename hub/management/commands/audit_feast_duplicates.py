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

Read-only.  ``--church`` scopes it; ``--verbose`` lists every stored name, not just the orphans.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

import armenian_lectionary

from hub.models import Church
from hub.services.feast_service import LECTIONARY_MAX_YEAR, LECTIONARY_MIN_YEAR


def engine_names(min_year=None, max_year=None):
    """Return every distinct English feast name the engine emits over the supported range.

    This is the set a re-keyed table can actually be reached by: after the migration a feast is
    looked up by the name the engine computes for the requested date, so a stored name outside
    this set is unreachable no matter what enrichment hangs off it.
    """
    min_year = LECTIONARY_MIN_YEAR if min_year is None else min_year
    max_year = LECTIONARY_MAX_YEAR if max_year is None else max_year
    names = set()
    day = datetime.date(min_year, 1, 1)
    end = datetime.date(max_year, 12, 31)
    while day <= end:
        result = armenian_lectionary.compute_armenian_lectionary(day)
        name = (result.get("Liturgical Day") or "").strip()
        if name:
            names.add(name)
        day += datetime.timedelta(days=1)
    return names


class Command(BaseCommand):
    help = (
        "Read-only: report stored feast names the lectionary engine no longer emits, whose "
        "enrichment is therefore unreachable by a date lookup."
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
            f"Enumerating engine names for {LECTIONARY_MIN_YEAR}-{LECTIONARY_MAX_YEAR}..."
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
                f"  {len(unreachable)} name(s) the engine never emits, so no date lookup reaches "
                f"them; their enrichment is stranded:"
            ))
            for name in unreachable:
                feast = by_name[name]
                # Say what would be lost if it were deleted, so the reader can judge whether to
                # rename it onto a current engine name or drop it.
                held = []
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

        if verbose:
            self.stdout.write("\n  all stored names:")
            for name in sorted(by_name):
                mark = " " if name in reachable else "!"
                self.stdout.write(f"    {mark} {name!r}")
