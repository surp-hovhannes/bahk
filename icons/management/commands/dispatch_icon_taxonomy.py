"""Explicit durable dispatch; does not install a scheduler."""

from argparse import RawDescriptionHelpFormatter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from icons.models import TaxonomyBudget
from icons.services.taxonomy_pipeline import due_work, process_icon


def configure_budget(options):
    name = options.get("budget") or getattr(settings, "ICON_TAXONOMY_BUDGET", "")
    if not name:
        raise CommandError("An explicit --budget or ICON_TAXONOMY_BUDGET is required")
    caps = (options.get("max_calls"), options.get("max_tokens"), options.get("max_spend"))
    if any(value is not None for value in caps):
        if not all(value is not None and Decimal(str(value)) > 0 for value in caps):
            raise CommandError("Supply all three positive caps: --max-calls, --max-tokens, --max-spend")
        values = dict(max_calls=caps[0], max_tokens=caps[1], max_microdollars=int(Decimal(str(caps[2])) * 1_000_000))
        budget, created = TaxonomyBudget.objects.get_or_create(name=name, defaults={**values, "enabled": True})
        if not created and any(getattr(budget, k) != v for k, v in values.items()):
            raise CommandError("Existing budget caps differ; counters/caps are never silently reset")
    else:
        budget = TaxonomyBudget.objects.filter(name=name, enabled=True).first()
        if not budget:
            raise CommandError("Budget missing/disabled; provision explicit cumulative caps")
    return name


def add_dispatch_arguments(parser):
    parser.add_argument("--budget", help="Enabled cumulative budget name; defaults to ICON_TAXONOMY_BUDGET.")
    parser.add_argument(
        "--max-calls",
        type=int,
        help="Positive cumulative wire-call cap; provision with both other caps, never reset counters.",
    )
    parser.add_argument(
        "--max-tokens", type=int, help="Positive cumulative reserved-token cap; requires both other caps."
    )
    parser.add_argument(
        "--max-spend",
        type=Decimal,
        help="Positive cumulative USD reservation cap; requires both other caps, no refunds.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Inline worker threads, 1..4 (default 1); ignored for enqueue/Celery, not a budget cap.",
    )


def run_inline(ids, *, budget, concurrency):
    if not 1 <= concurrency <= 4:
        raise CommandError("--concurrency must be 1..4")
    if concurrency == 1:
        return [process_icon(pk, budget_name=budget) for pk in ids]

    def run(pk):
        close_old_connections()
        try:
            return process_icon(pk, budget_name=budget)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(run, ids))


class CatalogueCommand(BaseCommand):
    def create_parser(self, prog_name, subcommand, **kwargs):
        kwargs["formatter_class"] = RawDescriptionHelpFormatter
        return super().create_parser(prog_name, subcommand, **kwargs)


COMMON_HELP = "Private catalogue taxonomy/evidence only: no title/tag/image selection or existing\nfeast/prayer assignment changes. Calendar independent; non-festal icons included.\nDispatch requires ICON_TAXONOMY_DISPATCH_ENABLED=true (default false), an enabled\n--budget or ICON_TAXONOMY_BUDGET, provider credentials, and a positive conservative\nICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS (default zero). Model/release settings are\nICON_TAXONOMY_MODEL and ICON_TAXONOMY_RELEASE; ICON_TAXONOMY_TIMEOUT bounds calls.\nICON_TAXONOMY_BEAT_ENABLED optionally enables recovery on existing Celery beat.\nProvision with ALL THREE positive cumulative calls/tokens/USD caps. Reservations\nacross stages, retries and timeouts are never reset, refunded or implicitly\nincreased. Existing budget caps must match; omit caps to reuse an enabled budget.\n--concurrency 1..4 limits inline threads only, not Celery workers or total calls.\nSee docs/ICON_TAXONOMY.md for storage, budgets, recovery and outcome inspection.\n"


class Command(CatalogueCommand):
    help = (
        """Dispatch pending/retry/expired-lease catalogue taxonomy work; disabled by default.
Optional --church limits dispatched rows; omission includes all churches. Due work
is ordered by availability then work PK, bounded by positive --limit (default 20).
Version reconciliation scans a bounded all-church batch before dispatch selection.
--inline runs synchronously here; omission queues Celery tasks and can be billable.
Output is processed/states for inline execution or dispatched count for Celery.
This command has no dry-run/checkpoint: use sibling backfill_icon_taxonomy.
Budget-blocked rows require backfill_icon_taxonomy --resume-budget-blocked; that
resets work attempts only, never budget counters, and cannot bypass exhausted caps.

Examples (dispatch examples require the settings and authorized budget below):
  python manage.py backfill_icon_taxonomy --dry-run --limit 100 --after-id 0
  python manage.py backfill_icon_taxonomy --limit 100 --after-id 0
  python manage.py backfill_icon_taxonomy --limit 100 --after-id "$NEXT_AFTER_ID"
  python manage.py dispatch_icon_taxonomy --inline --budget "$BUDGET_NAME" --limit 20
  # Provisioning placeholders: set BUDGET_NAME and all CAP_* to authorized values.
  python manage.py dispatch_icon_taxonomy --inline --budget "$BUDGET_NAME" --max-calls "$CAP_CALLS" --max-tokens "$CAP_TOKENS" --max-spend "$CAP_USD" --concurrency 1

Backfill JSON rows/next_after_id/states: pass next_after_id as exclusive --after-id;
stop when rows is empty. Dry-run writes nothing/calls no provider but may read
private storage. Backfill without --dispatch makes no direct provider calls, but
enqueue may wake enabled background workers and become billable.
"""
        + COMMON_HELP
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=20, help="Positive maximum due work rows per invocation (default 20)."
        )
        parser.add_argument(
            "--church",
            type=int,
            help="Optional church PK for dispatch; omit for all churches (reconciliation remains global).",
        )
        parser.add_argument(
            "--inline",
            action="store_true",
            help="Process synchronously in this process; otherwise queue Celery tasks (billable when enabled).",
        )
        add_dispatch_arguments(parser)

    def handle(self, **options):
        if not getattr(settings, "ICON_TAXONOMY_DISPATCH_ENABLED", False):
            self.stdout.write("disabled: ICON_TAXONOMY_DISPATCH_ENABLED=false")
            return
        if options["limit"] < 1:
            raise CommandError("--limit must be positive")
        budget = configure_budget(options)
        from icons.services.ingestion import reconcile_versions

        reconcile_versions(limit=options["limit"])
        qs = due_work()
        if options["church"]:
            qs = qs.filter(icon__church_id=options["church"])
        ids = list(qs.values_list("icon_id", flat=True)[: options["limit"]])
        if options["inline"]:
            states = run_inline(ids, budget=budget, concurrency=options["concurrency"])
            self.stdout.write(str({"processed": len(ids), "states": states}))
        else:
            from icons.tasks import analyze_icon_taxonomy

            for pk in ids:
                analyze_icon_taxonomy.delay(pk, budget_name=budget)
            self.stdout.write(f"dispatched={len(ids)}; database work remains recoverable")
