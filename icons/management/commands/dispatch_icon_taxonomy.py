"""Explicit durable dispatch; does not install a scheduler."""

from argparse import RawDescriptionHelpFormatter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from contextlib import contextmanager
import json
import math

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.test.utils import override_settings

from icons.models import TaxonomyBudget
from icons.services.taxonomy_pipeline import due_work, process_icon


def validate_options(options):
    if getattr(settings, "ICON_TAXONOMY_RELEASE", "catalogue-v2") == "catalogue-v1" and not (
        options.get("dry_run") or options.get("status")
    ):
        raise CommandError("Legacy release is immutable; configure a new catalogue-v2 release before writes")
    if options.get("limit", 1) < 1:
        raise CommandError("--limit must be positive")
    if not 1 <= options.get("concurrency", 1) <= 4:
        raise CommandError("--concurrency must be 1..4")
    if options.get("church") is not None and options["church"] < 1:
        raise CommandError("--church must be positive")
    if options.get("after_id", 0) < 0:
        raise CommandError("--after-id must be nonnegative")
    if options.get("through_id") is not None and options["through_id"] <= options.get("after_id", 0):
        raise CommandError("--through-id must exceed --after-id")
    ids = options.get("icon_ids")
    if ids is not None:
        if not ids or len(ids) > options.get("limit", 100) or any(pk < 1 for pk in ids) or len(set(ids)) != len(ids):
            raise CommandError("--icon-ids requires unique positive IDs within --limit")
        if options.get("after_id") or options.get("through_id"):
            raise CommandError("--icon-ids cannot combine with checkpoints")
    caps = (options.get("max_calls"), options.get("max_tokens"), options.get("max_spend"))
    if any(value is not None for value in caps):
        if not all(value is not None and Decimal(str(value)).is_finite() and Decimal(str(value)) > 0 for value in caps):
            raise CommandError("Supply all three finite positive caps: --max-calls, --max-tokens, --max-spend")
        if Decimal(str(caps[2])) * 1_000_000 < 1:
            raise CommandError("--max-spend must reserve at least one microdollar")
    inline = options.get("dispatch", False) or options.get("inline", False)
    if (options.get("enable_inline") or options.get("model")) and not inline:
        raise CommandError("--enable-inline and --model require explicit inline execution")
    if options.get("enable_inline") and not options.get("budget"):
        raise CommandError("--enable-inline requires an explicit --budget")
    if options.get("recover_unavailable") and not (ids or options.get("through_id")):
        raise CommandError("--recover-unavailable requires --icon-ids or --through-id")
    from icons.services.vision_provider import model_profile

    try:
        profile = model_profile(options.get("model"))
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    if inline and not (options.get("dry_run") or options.get("status")):
        rate = getattr(settings, "ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS", 0)
        if not math.isfinite(rate) or rate < profile["max_rate"]:
            raise CommandError("Reservation max rate must cover the selected model profile")


def configure_budget(options, *, write=True):
    validate_options(options)
    name = options.get("budget") or getattr(settings, "ICON_TAXONOMY_BUDGET", "")
    if not name:
        raise CommandError("An explicit --budget or ICON_TAXONOMY_BUDGET is required")
    budget = TaxonomyBudget.objects.filter(name=name).first()
    if budget and not budget.enabled:
        raise CommandError("Existing budget disabled; caps do not enable it")
    caps = (options.get("max_calls"), options.get("max_tokens"), options.get("max_spend"))
    if all(value is not None for value in caps):
        values = dict(max_calls=caps[0], max_tokens=caps[1], max_microdollars=int(Decimal(str(caps[2])) * 1_000_000))
        if budget and any(getattr(budget, k) != v for k, v in values.items()):
            raise CommandError("Existing budget caps differ; counters/caps are never silently reset")
        if not budget and write:
            TaxonomyBudget.objects.create(name=name, **values, enabled=True)
    elif not budget:
        raise CommandError("Budget missing/disabled; provision explicit cumulative caps")
    return name


@contextmanager
def execution_settings(options):
    changes = {}
    if options.get("enable_inline"):
        changes["ICON_TAXONOMY_DISPATCH_ENABLED"] = True
    if options.get("model"):
        changes["ICON_TAXONOMY_MODEL"] = options["model"]
    with override_settings(**changes):
        yield


def add_selection_arguments(parser):
    parser.add_argument(
        "--icon-ids",
        type=int,
        nargs="+",
        help="Exact unique icon IDs, at most --limit; cannot combine with checkpoints.",
    )
    parser.add_argument("--through-id", type=int, help="Inclusive upper icon PK bound.")


def add_dispatch_arguments(parser):
    from icons.services.vision_provider import MODEL_PROFILES

    parser.add_argument(
        "--model", choices=sorted(MODEL_PROFILES), help="Inline-only model; default remains configured Luna profile."
    )
    parser.add_argument(
        "--enable-inline",
        action="store_true",
        help="Enable dispatch only within this explicit inline invocation; requires --budget.",
    )
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


def run_inline(ids, *, budget, concurrency, reuse_observations=False):
    if not 1 <= concurrency <= 4:
        raise CommandError("--concurrency must be 1..4")
    if concurrency == 1:
        return [process_icon(pk, budget_name=budget, reuse_observations=reuse_observations) for pk in ids]

    from contextvars import copy_context

    contexts = {pk: copy_context() for pk in ids}

    def run(pk):
        close_old_connections()
        try:
            return contexts[pk].run(process_icon, pk, budget_name=budget, reuse_observations=reuse_observations)
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
Version reconciliation respects the same church/icon selection before dispatch.
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
            help="Optional church PK for reconciliation and dispatch; omit for all churches.",
        )
        parser.add_argument(
            "--inline",
            action="store_true",
            help="Process synchronously in this process; otherwise queue Celery tasks (billable when enabled).",
        )
        add_selection_arguments(parser)
        add_dispatch_arguments(parser)

    def handle(self, **options):
        validate_options(options)
        with execution_settings(options):
            self.run_batch(options)

    def run_batch(self, options):
        if not getattr(settings, "ICON_TAXONOMY_DISPATCH_ENABLED", False):
            self.stdout.write("disabled: ICON_TAXONOMY_DISPATCH_ENABLED=false")
            return
        from icons.models import Icon
        from icons.services.ingestion import reconcile_versions, inline_owned
        from icons.services.taxonomy_reporting import inspection, finalize_rows, budget_report
        from icons.services.taxonomy_inputs import versions
        from contextlib import nullcontext

        scope = Icon.objects.all()
        if options["church"] is not None:
            scope = scope.filter(church_id=options["church"])
        if options["icon_ids"] is not None:
            scope = scope.filter(pk__in=options["icon_ids"])
            if scope.count() != len(options["icon_ids"]):
                raise CommandError("Selected icons missing or outside church scope")
        if options["through_id"] is not None:
            scope = scope.filter(pk__lte=options["through_id"])
        budget = configure_budget(options)
        with inline_owned() if options["inline"] else nullcontext():
            if options["inline"]:
                from icons.services.ingestion import own_inline_work

                initial_ids = list(
                    due_work()
                    .filter(icon_id__in=scope.values("pk"))
                    .values_list("icon_id", flat=True)[: options["limit"]]
                )
                own_inline_work(initial_ids)
            reconcile_versions(
                limit=options["limit"] - len(initial_ids) if options["inline"] else options["limit"],
                church_id=options["church"],
                icon_ids=scope.values("pk"),
            )
            qs = due_work().filter(icon_id__in=scope.values("pk"))
            if options["inline"]:
                from icons.models import IconTaxonomyWork
                from icons.services.ingestion import INLINE_OWNED
                from django.db.models import Q

                qs = (
                    IconTaxonomyWork.objects.filter(icon_id__in=scope.values("pk"))
                    .filter(Q(pk__in=qs.values("pk")) | Q(state="inline_pending", lease_token=INLINE_OWNED.get()))
                    .order_by("available_at", "pk")
                )
            ids = list(qs.values_list("icon_id", flat=True)[: options["limit"]])
            if options["inline"]:
                from icons.services.ingestion import own_inline_work

                rows = []
                for icon in scope.filter(pk__in=ids).prefetch_related("tags"):
                    before = inspection(icon)
                    rows.append(
                        {
                            "id": icon.pk,
                            "before_state": before["state"],
                            "before_freshness": before["freshness"],
                            "action": "dispatch_due",
                        }
                    )
                ordered_rows = {row["id"]: row for row in rows}
                rows = [ordered_rows[pk] for pk in ids]
                own_inline_work(ids)
                states = run_inline(ids, budget=budget, concurrency=options["concurrency"])
                summary = finalize_rows(rows, states)
                self.stdout.write(
                    json.dumps(
                        {
                            "processed": summary.get("processed", 0),
                            "states": states,
                            "rows": rows,
                            "summary": summary,
                            "requested_versions": versions(),
                            **budget_report(budget),
                        }
                    )
                )
                if any(state in {"unavailable", "retry", "budget_blocked", "superseded"} for state in states):
                    raise CommandError("Inline batch incomplete; inspect JSON and resume selected work")
            else:
                from icons.tasks import analyze_icon_taxonomy

                for pk in ids:
                    analyze_icon_taxonomy.delay(pk, budget_name=budget)
                self.stdout.write(f"dispatched={len(ids)}; database work remains recoverable")
