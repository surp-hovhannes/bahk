"""Resumable ingestion and reconciliation, optionally explicitly budgeted dispatch."""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from icons.management.commands.dispatch_icon_taxonomy import add_dispatch_arguments, configure_budget, run_inline
from icons.models import Icon, IconTaxonomyProjection, IconTaxonomyWork
from icons.services.ingestion import schedule, refresh_content
from icons.services.taxonomy_inputs import dependencies_current, fingerprint, image_input, recover_filename, versions


class Command(BaseCommand):
    help = "Reconcile icon inputs and enqueue taxonomy; calendar independent. No model calls unless --dispatch."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--church", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--dispatch", action="store_true")
        parser.add_argument("--resume-budget-blocked", action="store_true")
        add_dispatch_arguments(parser)

    def handle(self, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be positive")
        if (
            options["dispatch"]
            and not options["dry_run"]
            and not getattr(settings, "ICON_TAXONOMY_DISPATCH_ENABLED", False)
        ):
            raise CommandError("Dispatch disabled; enqueue or dry-run remains available")
        budget = configure_budget(options) if options["dispatch"] and not options["dry_run"] else None
        qs = Icon.objects.filter(pk__gt=options["after_id"]).order_by("pk").prefetch_related("tags")
        if options["church"]:
            qs = qs.filter(church_id=options["church"])
        rows, ids = [], []
        for icon in qs[: options["limit"]]:
            p = IconTaxonomyProjection.objects.filter(icon=icon).select_related("analysis").first()
            work = IconTaxonomyWork.objects.filter(icon=icon).first()
            reason = "current"
            force = False
            if not work or work.fingerprint != fingerprint(icon):
                reason = "new_or_metadata_changed"
            if p:
                try:
                    changed = p.analysis.image_digest != image_input(icon)[0]
                except (OSError, ValueError):
                    changed = True
                if changed or p.analysis.versions != versions() or not dependencies_current(p.analysis.dependencies):
                    force, reason = True, "image_or_versions_changed"
            elif work and work.state in {"complete", "unavailable"}:
                latest = icon.taxonomic_analyses.order_by("-created_at").first()
                if latest and (latest.versions != versions() or not dependencies_current(latest.dependencies)):
                    force, reason = True, "versions_changed"
            recovered = recover_filename(icon.image.name) if not icon.original_filename else ""
            if recovered:
                reason = "recover_storage_stem"
            if not options["dry_run"]:
                refresh_content(icon)
                if recovered:
                    Icon.objects.filter(pk=icon.pk).update(
                        original_filename=recovered, filename_provenance="recovered_storage_stem"
                    )
                schedule(icon.pk, force=force)
                if options["resume_budget_blocked"]:
                    IconTaxonomyWork.objects.filter(icon=icon, state="budget_blocked").update(
                        state="pending", attempts=0, available_at=timezone.now(), error=""
                    )
                if options["dispatch"]:
                    IconTaxonomyWork.objects.filter(icon=icon, state="pending").update(available_at=timezone.now())
                ids.append(icon.pk)
            rows.append({"id": icon.pk, "reason": reason, "state": work.state if work else "missing"})
        states = run_inline(ids, budget=budget, concurrency=options["concurrency"]) if budget else []
        self.stdout.write(
            json.dumps(
                {
                    "dry_run": options["dry_run"],
                    "rows": rows,
                    "next_after_id": rows[-1]["id"] if rows else options["after_id"],
                    "states": states,
                }
            )
        )
