"""Explicit durable dispatch; does not install a scheduler."""

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
    parser.add_argument("--budget")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-spend", type=Decimal, help="USD, cumulative conservative reservations")
    parser.add_argument("--concurrency", type=int, default=1)


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


class Command(BaseCommand):
    help = "Dispatch pending/retry/expired-lease taxonomy work; disabled until configured."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--church", type=int)
        parser.add_argument("--inline", action="store_true", help="Process directly without a broker")
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
