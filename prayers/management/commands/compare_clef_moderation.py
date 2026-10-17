"""Read-only comparison of Clef moderation against past automated decisions.

Re-moderates already-reviewed prayer requests with Clef and tabulates its
routing (approve / reject / review / escalate) against the decision the LLM
made at the time. Requests rejected by the profanity filter or whose LLM call
failed are skipped, since their stored model decision is not the effective
route. Safety escalations remain comparable even when profanity is present.
Nothing is written to the database.
"""

from collections import Counter

from django.core.management.base import BaseCommand

from hub.services.clef import CLEF_MODELS, ClefError
from prayers.clef_moderation import clef_moderation_result
from prayers.models import PrayerRequest

ROUTES = ("approve", "reject", "review", "escalate")


def route(result):
    """Map a moderation result dict to the branch moderate_prayer_request_task takes."""
    severity = str(result.get("severity") or "low").strip().lower()
    action = str(result.get("suggested_action") or "").strip().lower()
    if severity == "critical" or action == "escalate":
        return "escalate"
    if severity == "high" or result.get("requires_human_review") or action == "flag_for_review":
        return "review"
    return "approve" if result.get("approved") else "reject"


class Command(BaseCommand):
    help = "Compare Clef prayer request moderation against past LLM decisions (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200, help="Most recent N moderated requests (default 200)")
        parser.add_argument("--model", choices=CLEF_MODELS, default="clef")
        parser.add_argument(
            "--show-text",
            action="store_true",
            help="Include request titles in the disagreement list (they may contain personal details)",
        )

    def handle(self, *args, limit, model, show_text, **options):
        requests = PrayerRequest.objects.filter(moderated_at__isnull=False).order_by("-moderated_at")[:limit]

        table = Counter()
        disagreements = []
        skipped = Counter()
        for prayer_request in requests:
            stored = prayer_request.moderation_result or {}
            llm_check = stored.get("llm_check")
            if not isinstance(llm_check, dict) or "error" in llm_check or "approved" not in llm_check:
                skipped["no usable LLM decision"] += 1
                continue
            if llm_check.get("engine") == "clef":
                skipped["already moderated by Clef"] += 1
                continue
            before = route(llm_check)
            profanity_check = stored.get("profanity_check")
            if isinstance(profanity_check, dict) and profanity_check.get("passed") is False and before != "escalate":
                skipped["profanity rejection"] += 1
                continue
            try:
                clef_result = clef_moderation_result(prayer_request, model=model)
            except ClefError as exc:
                skipped["Clef error"] += 1
                self.stderr.write(f"#{prayer_request.id}: {exc}")
                continue

            after = route(clef_result)
            table[before, after] += 1
            if before != after:
                disagreements.append((prayer_request, before, after, clef_result["probabilities"]))

        compared = sum(table.values())
        agreed = sum(count for (before, after), count in table.items() if before == after)
        self.stdout.write(f"Compared {compared} requests with {model}; skipped {dict(skipped) or 0}")
        if not compared:
            return
        self.stdout.write(f"Agreement: {agreed}/{compared} ({agreed / compared:.0%})\n")
        self.stdout.write("rows = previous LLM route, columns = Clef route")
        self.stdout.write(f"{'':>10}" + "".join(f"{name:>10}" for name in ROUTES))
        for before in ROUTES:
            self.stdout.write(f"{before:>10}" + "".join(f"{table[before, after]:>10}" for after in ROUTES))

        if disagreements:
            self.stdout.write("\nDisagreements:")
        for prayer_request, before, after, probabilities in disagreements:
            scores = " ".join(f"{name}={value:.2f}" for name, value in probabilities.items())
            title = f" {prayer_request.title!r}" if show_text else ""
            self.stdout.write(
                f"  #{prayer_request.id} [{prayer_request.status}] {before} -> {after}{title}\n      {scores}"
            )
