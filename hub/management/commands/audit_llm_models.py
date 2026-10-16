"""Report which LLM model IDs saved prompts and code defaults use, and their lifecycle.

Read-only. Prints IDs, counts and lifecycle only, never prompt text, so the output is
safe to paste into an issue. ``--strict`` exits non-zero when an active prompt uses
a deprecated or retired model, for post-rollout checks.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from hub.models import LLMPrompt
from hub.services import llm_models
from hub.services.llm_models import ACTIVE, UnsupportedModelError, lifecycle_for, resolve_model


class Command(BaseCommand):
    help = "Audit LLM model IDs on saved prompts and code defaults against their provider lifecycle."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with an error if any active prompt uses a deprecated or retired model.",
        )

    def handle(self, *args, **options):
        rows = (
            LLMPrompt.objects.values("model", "applies_to", "active")
            .annotate(prompts=Count("id"))
            .order_by("model", "applies_to", "-active")
        )
        self.stdout.write("Saved prompts (model, applies_to, active, count, status, shutdown, sends):")
        affected_active = []
        for row in rows:
            lifecycle = lifecycle_for(row["model"])
            try:
                sends = resolve_model(row["model"])
            except UnsupportedModelError:
                sends = "ERROR: no replacement"
            self.stdout.write(
                f"  {row['model']}  {row['applies_to']}  active={row['active']}  n={row['prompts']}  "
                f"{lifecycle.status}  {lifecycle.shutdown or '-'}  {sends}"
            )
            if row["active"] and lifecycle.status != ACTIVE:
                affected_active.append(row)

        self.stdout.write("Code defaults:")
        defaults = {
            "DEFAULT_CLAUDE_MODEL": llm_models.DEFAULT_CLAUDE_MODEL,
            "DEFAULT_CLAUDE_CLASSIFIER_MODEL": llm_models.DEFAULT_CLAUDE_CLASSIFIER_MODEL,
            "DEFAULT_MODERATION_MODEL": llm_models.DEFAULT_MODERATION_MODEL,
            "ICON_MATCH_MODEL": getattr(settings, "ICON_MATCH_MODEL", "gpt-4.1-mini"),
            "ICON_TAXONOMY_MODEL": getattr(settings, "ICON_TAXONOMY_MODEL", "gpt-5.6-luna"),
        }
        for name, model in defaults.items():
            self.stdout.write(f"  {name} = {model}  {lifecycle_for(model).status}")

        if affected_active:
            self.stdout.write(self.style.WARNING(f"{len(affected_active)} active prompt group(s) need a new model."))
            if options["strict"]:
                raise CommandError("Active prompts use deprecated or retired models.")
        else:
            self.stdout.write(self.style.SUCCESS("No active prompt uses a deprecated or retired model."))
