"""Report what re-keying Feast from (day, name) to (church, name) would do to real data.

``Feast`` rows are keyed to a ``Day``, so the same commemoration gets a fresh row -- and a fresh
LLM-generated context, icon match and designation -- on every recurrence.  The engine emits only a
few hundred distinct names over its whole supported range, so those rows are overwhelmingly
duplicates of each other.

This command is read-only.  It exists to be run against production *before* the migration that
collapses them, because the merge has to pick a single survivor per commemoration and the cost of
picking wrong is losing curated content.  It reports:

  * how far the table would shrink, per church;
  * which contexts survive and which are deactivated, under the same rule the migration uses
    (newest active context wins, thumbs are summed, first non-null icon/designation);
  * conflicts a human should look at -- duplicate names carrying *different* icons or
    designations, where the merge has to discard one;
  * names in the database the engine no longer emits, which would keep their enrichment but never
    be reached again by a date lookup.

Nothing is written.  Run ``--verbose`` for the per-name detail, ``--church`` to scope it.
"""
import collections
import datetime

from django.core.management.base import BaseCommand, CommandError

import armenian_lectionary
from armenian_lectionary import MAX_YEAR, MIN_YEAR

from hub.models import Church, Feast


def engine_names(min_year=None, max_year=None):
    """Return every distinct English feast name the engine emits over the supported range.

    This is the set a re-keyed table can actually be reached by: after the migration a feast is
    looked up by the name the engine computes for the requested date, so a stored name outside
    this set is unreachable no matter what enrichment hangs off it.
    """
    min_year = MIN_YEAR if min_year is None else min_year
    max_year = MAX_YEAR if max_year is None else max_year
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


def survivor(feasts):
    """Pick the row a group of same-named feasts collapses onto, and describe the merge.

    Mirrors the migration's rule exactly, so this report is a dry run of it rather than an
    independent guess:

      * the surviving *context* is the newest active one across the group (falling back to the
        newest of any state), so the most recently generated text is what readers keep seeing;
      * thumbs are summed across every context in the group, so feedback is never dropped;
      * icon and designation are the first non-null in ``id`` order, which is the oldest row --
        the one an admin is most likely to have curated by hand.

    Returns a dict describing the merge; it does not touch the database.
    """
    feasts = sorted(feasts, key=lambda f: f.id)
    contexts = [ctx for feast in feasts for ctx in feast.contexts.all()]

    active = [c for c in contexts if c.active]
    pool = active or contexts
    # time_of_generation is nullable (auto_now_add was added after the model), so fall back to id
    # ordering for rows written before it existed rather than letting None sort unpredictably.
    keeper = max(pool, key=lambda c: (c.time_of_generation is not None,
                                      c.time_of_generation or datetime.datetime.min, c.id),
                 default=None)

    icons = [f.icon_id for f in feasts if f.icon_id]
    designations = [f.designation for f in feasts if f.designation]

    return {
        "keep": feasts[0],
        "absorbed": feasts[1:],
        "context_kept": keeper,
        "contexts_deactivated": [c for c in contexts if keeper and c.id != keeper.id],
        "thumbs_up": sum(c.thumbs_up for c in contexts),
        "thumbs_down": sum(c.thumbs_down for c in contexts),
        "icon_id": icons[0] if icons else None,
        "icon_conflict": len(set(icons)) > 1,
        "designation": designations[0] if designations else None,
        "designation_conflict": len(set(designations)) > 1,
    }


class Command(BaseCommand):
    help = (
        "Read-only: report what collapsing Feast rows onto (church, name) would do. Run this "
        "against production before the re-key migration; it writes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("--church", default=None,
                            help="Limit the report to one church, by name (default: all).")
        parser.add_argument("--verbose", action="store_true",
                            help="List every duplicated name, not just the totals.")
        parser.add_argument("--skip-engine", action="store_true",
                            help="Skip the reachability check, which sweeps the whole supported "
                                 "range and takes a few seconds.")

    def handle(self, *args, **options):
        churches = Church.objects.all()
        if options["church"]:
            churches = churches.filter(name=options["church"])
            if not churches.exists():
                raise CommandError(f"No church named {options['church']!r}.")

        reachable = None
        if not options["skip_engine"]:
            self.stdout.write(
                f"Enumerating engine names for {MIN_YEAR}-{MAX_YEAR}..."
            )
            reachable = engine_names()
            self.stdout.write(f"  engine emits {len(reachable)} distinct names.\n")

        for church in churches:
            self._report_church(church, reachable, options["verbose"])

    def _report_church(self, church, reachable, verbose):
        feasts = list(
            Feast.objects.filter(day__church=church)
            .select_related("day")
            .prefetch_related("contexts")
        )
        if not feasts:
            self.stdout.write(f"\n{church.name}: no feasts.")
            return

        by_name = collections.defaultdict(list)
        for feast in feasts:
            by_name[feast.name].append(feast)

        duplicated = {name: group for name, group in by_name.items() if len(group) > 1}
        merges = {name: survivor(group) for name, group in by_name.items()}

        removed = sum(len(m["absorbed"]) for m in merges.values())
        deactivated = sum(len(m["contexts_deactivated"]) for m in merges.values())
        icon_conflicts = {n: m for n, m in merges.items() if m["icon_conflict"]}
        desig_conflicts = {n: m for n, m in merges.items() if m["designation_conflict"]}

        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{church.name}"))
        self.stdout.write(
            f"  {len(feasts)} rows -> {len(by_name)} commemorations "
            f"({removed} absorbed, {len(duplicated)} names duplicated)"
        )
        self.stdout.write(f"  contexts deactivated by the merge: {deactivated}")

        if icon_conflicts:
            self.stdout.write(self.style.WARNING(
                f"  {len(icon_conflicts)} name(s) carry more than one icon; the merge keeps the "
                f"oldest row's and discards the rest:"
            ))
            for name in sorted(icon_conflicts)[:10]:
                icons = sorted({f.icon_id for f in by_name[name] if f.icon_id})
                self.stdout.write(f"      {name!r}: icons {icons} -> {icons[0]}")

        if desig_conflicts:
            self.stdout.write(self.style.WARNING(
                f"  {len(desig_conflicts)} name(s) carry more than one designation; the merge "
                f"keeps the oldest row's:"
            ))
            for name in sorted(desig_conflicts)[:10]:
                values = sorted({f.designation for f in by_name[name] if f.designation})
                self.stdout.write(f"      {name!r}: {values}")

        if reachable is not None:
            unreachable = sorted(set(by_name) - reachable)
            if unreachable:
                self.stdout.write(self.style.WARNING(
                    f"  {len(unreachable)} stored name(s) the engine never emits, so a date "
                    f"lookup will not reach them after the re-key:"
                ))
                for name in unreachable[:15]:
                    dates = sorted(f.day.date for f in by_name[name])
                    self.stdout.write(
                        f"      {name!r} ({len(dates)} row(s), {dates[0]}..{dates[-1]})"
                    )
                if len(unreachable) > 15:
                    self.stdout.write(f"      ... and {len(unreachable) - 15} more")
            else:
                self.stdout.write("  every stored name is one the engine still emits.")

        if verbose and duplicated:
            self.stdout.write("\n  duplicated names:")
            for name in sorted(duplicated, key=lambda n: -len(duplicated[n])):
                merge = merges[name]
                kept = merge["context_kept"]
                self.stdout.write(
                    f"    {len(duplicated[name]):4d}x {name!r}"
                    f" -> feast #{merge['keep'].id}"
                    f", context #{kept.id if kept else None}"
                    f", thumbs +{merge['thumbs_up']}/-{merge['thumbs_down']}"
                )
