"""Score one profile against hand-labelled cases. Billable calls require --live.

Unlike the paired audit, this command has an opinion about correctness: each case
carries reviewer labels, so a run reports accuracy rather than only agreement.
Labels are editorial judgements about a catalogue, not ground truth - extend
`--cases-json` as the reviewer's opinion sharpens.
"""

import json
import time
from dataclasses import replace
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from hub.services.icon_match_profiles import REGISTERED_PROFILES
from hub.services.icon_match_service import MatchLimits, match_icons, provider_for
from hub.services.icon_matching import IconMatchRequest

# A case is {"feast": str, "correct": [icon_id], "acceptable": [icon_id]}.
# Both lists empty means the catalogue holds nothing suitable and returning
# nothing is the right answer.
VERDICTS = ("exact", "acceptable", "wrong", "silent", "error")


def verdict(case, matches):
    """Classify the top-ranked recommendation against the case's labels."""
    correct, acceptable = set(case.get("correct", ())), set(case.get("acceptable", ()))
    if not matches:
        return "silent"
    top = matches[0]["id"]
    if top in correct:
        return "exact"
    if top in acceptable:
        return "acceptable"
    return "silent" if not correct and not acceptable and not matches else "wrong"


def score(case, matches):
    """Summarise one case, keeping assignment separate from ranking."""
    correct, acceptable = set(case.get("correct", ())), set(case.get("acceptable", ()))
    assigned = [m["id"] for m in matches if m["auto_assignable"]]
    return {
        "feast": case["feast"],
        "expectation": "exact_exists" if correct else ("fallback_only" if acceptable else "nothing_suitable"),
        "verdict": verdict(case, matches),
        "top_id": matches[0]["id"] if matches else None,
        "returned": len(matches),
        "auto_assigned": assigned,
        # The costly error: assigning where the reviewer says nothing fits.
        "unsafe_assignment": bool(assigned) and not (set(assigned) & (correct | acceptable)),
    }


def evaluate_cases(catalogue, cases, profile, *, live=False, arm_timeout=300):
    results = []
    limits = replace(MatchLimits(), positive_limit=profile.positive_limit, total_seconds=arm_timeout)
    for case in cases:
        started = time.monotonic()
        try:
            if not live:
                raise RuntimeError("offline")
            outcome = match_icons(
                catalogue,
                IconMatchRequest(
                    kind="feast",
                    primary_text=case["feast"],
                    auto_assign_policy="feast_strict",
                    max_results=case.get("max_results", 5),
                ),
                provider=provider_for(profile),
                limits=limits,
                profile=profile,
            )
            row = {
                **score(case, outcome.matches),
                "status": outcome.status,
                "assessed": outcome.assessed_count,
                "catalogue_count": outcome.catalogue_count,
                "diagnostics": outcome.diagnostics,
                "matches": outcome.matches,
            }
        except Exception:
            # Never surface provider errors or credentials into the report.
            row = {**score(case, []), "verdict": "error", "status": "unavailable", "diagnostics": ["arm_failed"]}
        row["elapsed_seconds"] = round(time.monotonic() - started, 1)
        results.append(row)
    tally = {v: sum(1 for r in results if r["verdict"] == v) for v in VERDICTS}
    return {
        "profile": profile.metadata(),
        "case_count": len(cases),
        "verdicts": tally,
        "complete_count": sum(1 for r in results if r.get("status") == "complete"),
        "unsafe_assignment_count": sum(1 for r in results if r["unsafe_assignment"]),
        "auto_assigned_count": sum(1 for r in results if r["auto_assigned"]),
        "cases": results,
    }


class Command(BaseCommand):
    help = "Score one registered profile against labelled cases. Billable calls require --live."

    def add_arguments(self, parser):
        for name in ("catalogue-json", "cases-json", "output-json"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--profile", required=True, choices=tuple(REGISTERED_PROFILES))
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--arm-timeout", type=float, default=300)

    def handle(self, *args, **options):
        try:
            output = Path(options["output_json"])
            inputs = {Path(options[k]).resolve() for k in ("catalogue_json", "cases_json")}
            if output.resolve() in inputs:
                raise ValueError("Output must not overwrite an input")
            catalogue = json.loads(Path(options["catalogue_json"]).read_text())
            cases = json.loads(Path(options["cases_json"]).read_text())
            if not isinstance(catalogue, list) or not isinstance(cases, list):
                raise ValueError("Catalogue and cases must be arrays")
            report = evaluate_cases(
                catalogue,
                cases,
                REGISTERED_PROFILES[options["profile"]],
                live=options["live"],
                arm_timeout=options["arm_timeout"],
            )
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError("Invalid case evaluation inputs") from exc
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k != "cases"}))
