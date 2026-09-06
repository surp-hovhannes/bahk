"""Scoped, resumable reconciliation with read-only inspection and inline ownership."""

import json
from contextlib import nullcontext

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone
from django.db.models import Q

from icons.management.commands.dispatch_icon_taxonomy import (
    CatalogueCommand,
    COMMON_HELP,
    add_dispatch_arguments,
    add_selection_arguments,
    configure_budget,
    execution_settings,
    run_inline,
    validate_options,
)
from icons.models import Icon, IconTaxonomyWork
from icons.services.ingestion import schedule, refresh_content, inline_owned, own_inline_work, INLINE_OWNED
from icons.services.taxonomy_reconciliation import reconcile_selected_projections
from icons.services.taxonomy_reporting import inspection, finalize_rows, budget_report
from icons.services.taxonomy_inputs import dependencies_current, fingerprint, image_input, recover_filename, versions


class Command(CatalogueCommand):
    help = (
        """Reconcile private icon evidence with exact bounded selection and safe inline execution.
--dry-run previews storage/input reconciliation without writes, budgets or provider calls.
--status inspects persisted freshness without storage reads, writes or provider calls.
--icon-ids selects an exact cohort; alternatively use exclusive --after-id and inclusive
--through-id. Ascending PK batches are bounded by --limit. Resume at next_after_id.
Without --dispatch enqueue can wake enabled background workers and become billable.
--dispatch owns selected work inline, suppresses queue wakes and requires a budget.
--enable-inline enables only this process and restores settings on every exit.
--recover-unavailable requires bounded selection; retries only changed analysis identities.
Unchanged malformed evidence or unavailable images are never blindly reset. Retained
observations may be reused only with a compatible image/model/observation contract.
--resume-inline reclaims expired inline ownership within exact selection after a crash.
--resume-budget-blocked resets work attempts, never cumulative reservations.
JSON rows report before_state, action, after_state, processing_result, analysis_id,
assertion counts, freshness and sanitized diagnostics. A final selected projection pass
reconciles dependency growth from retained evidence without provider calls; summary
fresh/stale counts describe that final state. Reservations are not actual cost.
Partial execution failures emit full JSON then exit nonzero. Inspect/resume failed IDs;
checkpoint pagination alone does not retry a failed earlier row. Disable pilot budgets
explicitly after use; this command never silently enables an existing disabled budget.

Examples:
  python manage.py backfill_icon_taxonomy --dry-run --icon-ids 1 2 3
  python manage.py backfill_icon_taxonomy --status --through-id 50 --limit 50
  python manage.py backfill_icon_taxonomy --dispatch --enable-inline --budget "$BUDGET_NAME" --icon-ids 1 2 3 --model gpt-5.6-luna
  python manage.py backfill_icon_taxonomy --recover-unavailable --dispatch --enable-inline --budget "$BUDGET_NAME" --icon-ids 1 2 3
  python manage.py backfill_icon_taxonomy --dry-run --after-id "$NEXT_AFTER_ID" --through-id 500 --limit 100
"""
        + COMMON_HELP
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--status", action="store_true", help="Read-only persisted inspection; no storage/provider calls."
        )
        parser.add_argument("--church", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--dispatch", action="store_true", help="Own selected work and process inline.")
        parser.add_argument("--resume-budget-blocked", action="store_true")
        parser.add_argument("--recover-unavailable", action="store_true")
        parser.add_argument(
            "--resume-inline", action="store_true", help="Reclaim expired abandoned inline work in bounded selection."
        )
        add_selection_arguments(parser)
        add_dispatch_arguments(parser)

    def handle(self, **options):
        validate_options(options)
        if any(options.get(key) is not None for key in ("max_calls", "max_tokens", "max_spend")) and not (
            options["dispatch"] or options["dry_run"]
        ):
            raise CommandError("Budget caps require --dispatch or a read-only --dry-run preview")
        if options["resume_inline"] and not (options["icon_ids"] or options["through_id"]):
            raise CommandError("--resume-inline requires bounded selection")
        with execution_settings(options):
            self.run_batch(options)

    def run_batch(self, options):
        readonly = options["dry_run"] or options["status"]
        if options["dispatch"] and not readonly and not settings.ICON_TAXONOMY_DISPATCH_ENABLED:
            raise CommandError("Dispatch disabled; use explicit --dispatch --enable-inline --budget")
        qs = Icon.objects.filter(pk__gt=options["after_id"]).order_by("pk").prefetch_related("tags")
        if options["church"] is not None:
            qs = qs.filter(church_id=options["church"])
        if options["through_id"] is not None:
            qs = qs.filter(pk__lte=options["through_id"])
        if options["icon_ids"] is not None:
            qs = qs.filter(pk__in=options["icon_ids"])
            if qs.count() != len(options["icon_ids"]):
                raise CommandError("Selected icons missing or outside church scope")
        # Read-only budget validation is before storage mutation/provisioning.
        budget = None
        if (options["dispatch"] or options["resume_budget_blocked"]) and not readonly:
            checked_budget = configure_budget(options, write=False)
            if options["dispatch"]:
                budget = checked_budget
        icons = list(qs[: options["limit"]])
        rows, ids = [], []
        if budget:
            configure_budget(options)
        before_rows = {icon.pk: inspection(icon) for icon in icons}
        with inline_owned() if options["dispatch"] and not readonly else nullcontext():
            if budget:
                own_inline_work(
                    [icon.pk for icon in icons],
                    resume_budget_blocked=options["resume_budget_blocked"],
                    resume_inline=options["resume_inline"],
                )
            for icon in icons:
                before = before_rows[icon.pk]
                work = IconTaxonomyWork.objects.filter(icon=icon).first()
                latest = icon.taxonomic_analyses.filter(pk=before["analysis_id"]).first()
                force, reason = False, "current"
                if work and work.state in {"pending", "retry"}:
                    reason = "process_due"
                if not work or work.fingerprint != fingerprint(icon):
                    reason = "new_or_metadata_changed"
                stale = latest and (latest.versions != versions() or not dependencies_current(latest.dependencies))
                if stale and (not work or work.state != "unavailable" or options["recover_unavailable"]):
                    force, reason = True, "analysis_identity_changed"
                if work and work.state == "complete" and before["freshness"]:
                    force, reason = True, "reconcile_stale_projection"
                busy = bool(
                    work
                    and work.state in {"running", "inline_pending", "inline_running"}
                    and work.lease_until
                    and work.lease_until > timezone.now()
                    and not (work.state == "inline_pending" and work.lease_token == INLINE_OWNED.get())
                )
                if busy:
                    force, reason = False, "active_lease_not_due"
                if not busy and work:
                    if options["resume_budget_blocked"] and before["state"] == "budget_blocked":
                        reason = "resume_budget_blocked"
                    if options["resume_inline"] and before["state"] in {
                        "inline_pending",
                        "inline_running",
                        "inline_retry",
                    }:
                        reason = "resume_inline_when_due"
                    if options["recover_unavailable"] and work.state == "unavailable" and stale:
                        reason = "recover_versioned_analysis"
                recovered = ""
                if not options["status"] and not busy:
                    recovered = recover_filename(icon.image.name) if not icon.original_filename else ""
                    if recovered:
                        reason = "recover_storage_stem"
                    try:
                        changed = icon.image_content_digest != image_input(icon)[0]
                    except (OSError, ValueError):
                        changed = False
                    if changed:
                        force, reason = True, "image_content_changed"
                if work and work.state == "unavailable" and not force:
                    reason = "unavailable_unchanged_no_retry"
                if not readonly and not busy:
                    refresh_content(icon)
                    if recovered:
                        Icon.objects.filter(pk=icon.pk).update(
                            original_filename=recovered, filename_provenance="recovered_storage_stem"
                        )
                    schedule(icon.pk, force=force)
                    if options["resume_budget_blocked"] and not options["dispatch"]:
                        IconTaxonomyWork.objects.filter(icon=icon, state="budget_blocked").update(
                            state="pending", attempts=0, available_at=timezone.now(), error=""
                        )
                    if options["resume_inline"] and not options["dispatch"]:
                        IconTaxonomyWork.objects.filter(icon=icon).filter(
                            Q(state__in=["inline_pending", "inline_running"], lease_until__lte=timezone.now())
                            | Q(state="inline_retry", available_at__lte=timezone.now())
                        ).update(state="pending", lease_token="", lease_until=None, available_at=timezone.now())
                if not readonly:
                    ids.append(icon.pk)
                rows.append(
                    {
                        "id": icon.pk,
                        "before_state": before["state"],
                        "action": reason,
                        "before_freshness": before["freshness"],
                    }
                )
            states = (
                run_inline(
                    ids,
                    budget=budget,
                    concurrency=options["concurrency"],
                    reuse_observations=options["recover_unavailable"],
                )
                if budget
                else []
            )
        if not readonly:
            reconcile_selected_projections(ids)
        summary = finalize_rows(rows, states, readonly=readonly)
        for row in rows:
            key = "stale" if row["freshness"] else "fresh"
            summary[key] = summary.get(key, 0) + 1
        self.stdout.write(
            json.dumps(
                {
                    "dry_run": options["dry_run"],
                    "status": options["status"],
                    "rows": rows,
                    "next_after_id": rows[-1]["id"] if rows else options["after_id"],
                    "states": states,
                    "summary": summary,
                    **budget_report(budget or options.get("budget")),
                    "requested_versions": versions(),
                }
            )
        )
        if summary.get("failed") or summary.get("blocked"):
            raise CommandError("Inline batch incomplete; inspect JSON and resume selected failed/blocked IDs")
