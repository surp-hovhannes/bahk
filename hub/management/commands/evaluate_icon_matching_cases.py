"""Score one profile against hand-labelled cases. Billable calls require --live.

Unlike the paired audit, this command has an opinion about correctness: each case
carries reviewer labels, so a run reports accuracy rather than only agreement.
Labels are editorial judgements about a catalogue, not ground truth - extend
`--cases-json` as the reviewer's opinion sharpens, then `--rescore-json` an earlier
report to see what the new labels say about it without paying for another run.
"""

import json
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from hub.services.icon_match_profiles import REGISTERED_PROFILES
from hub.services.icon_match_service import MatchLimits, OpenAIIconProvider, match_icons
from hub.services.icon_matching import IconMatchRequest

# A case is {"feast": str, "correct": [id], "acceptable": [id], "assignable": [id]}.
# correct: depicts the commemoration. acceptable: a reasonable suggestion when
# nothing correct exists. assignable: may be written to the feast without a human;
# defaults to correct, so a fallback is only assignable when a reviewer says so.
# All empty means the catalogue holds nothing suitable and silence is right.
VERDICTS = ("exact", "acceptable", "wrong", "abstained", "missed", "error")
DESIRABLE = ("exact", "acceptable", "abstained")
EXPECTATIONS = ("exact_exists", "fallback_only", "nothing_suitable")


def expectation(case):
    if case.get("correct"):
        return "exact_exists"
    return "fallback_only" if case.get("acceptable") else "nothing_suitable"


def verdict(case, matches):
    """Classify the top-ranked recommendation against the case's labels."""
    if not matches:
        return "abstained" if expectation(case) == "nothing_suitable" else "missed"
    top = matches[0]["id"]
    if top in set(case.get("correct", ())):
        return "exact"
    if top in set(case.get("acceptable", ())):
        return "acceptable"
    return "wrong"


def assigned_id(status, matches):
    """The icon production would write: hub/tasks/icon_tasks.py writes only a
    complete outcome's first match, and only when it is auto_assignable."""
    if status == "complete" and matches and matches[0].get("auto_assignable") is True:
        return matches[0]["id"]
    return None


def score(case, status, matches, *, failed=False):
    """Summarise one case, keeping assignment separate from ranking."""
    assignable = set(case.get("assignable", case.get("correct", ())))
    assigned = None if failed else assigned_id(status, matches)
    return {
        "feast": case["feast"],
        "expectation": expectation(case),
        "verdict": "error" if failed else verdict(case, matches),
        "top_id": matches[0]["id"] if matches and not failed else None,
        "returned": 0 if failed else len(matches),
        "assigned_id": assigned,
        # The costly error: writing an icon the reviewer did not mark assignable.
        "unsafe_assignment": assigned is not None and assigned not in assignable,
    }


def summarise(rows):
    tally = Counter(r["verdict"] for r in rows)
    by_expectation = {
        name: {v: n for v, n in Counter(r["verdict"] for r in rows if r["expectation"] == name).items()}
        for name in EXPECTATIONS
    }
    return {
        "case_count": len(rows),
        "verdicts": {v: tally.get(v, 0) for v in VERDICTS},
        "desirable_count": sum(tally.get(v, 0) for v in DESIRABLE),
        "by_expectation": by_expectation,
        "complete_count": sum(1 for r in rows if r.get("status") == "complete"),
        "assigned_count": sum(1 for r in rows if r["assigned_id"] is not None),
        "unsafe_assignment_count": sum(1 for r in rows if r["unsafe_assignment"]),
        "cases": rows,
    }


def evaluate_cases(catalogue, cases, profile, *, provider_factory=None, arm_timeout=None):
    """Run every case live. Limits match the Celery feast path unless arm_timeout is given."""
    provider_factory = provider_factory or (lambda p: OpenAIIconProvider(profile=p))
    limits = replace(MatchLimits(), positive_limit=profile.positive_limit)
    if arm_timeout is not None:
        limits = replace(limits, total_seconds=arm_timeout)
    rows = []
    for case in cases:
        started = time.monotonic()
        try:
            outcome = match_icons(
                catalogue,
                IconMatchRequest(
                    kind="feast",
                    primary_text=case["feast"],
                    auto_assign_policy="feast_strict",
                    max_results=case.get("max_results", 5),
                ),
                provider=provider_factory(profile),
                limits=limits,
                profile=profile,
            )
            row = {
                **score(case, outcome.status, outcome.matches),
                "status": outcome.status,
                "assessed": outcome.assessed_count,
                "catalogue_count": outcome.catalogue_count,
                "diagnostics": outcome.diagnostics,
                "matches": outcome.matches,
            }
        except Exception:
            # Never surface provider errors or credentials into the report.
            row = {
                **score(case, "unavailable", [], failed=True),
                "status": "unavailable",
                "diagnostics": ["arm_failed"],
            }
        row["elapsed_seconds"] = round(time.monotonic() - started, 1)
        rows.append(row)
    return {"profile": profile.metadata(), **summarise(rows)}


def rescore(report, cases):
    """Re-score a saved report (or a bare list of outcome rows) against current labels."""
    rows = report["cases"] if isinstance(report, dict) else report
    by_feast = {case["feast"]: case for case in cases}
    rescored = []
    for row in rows:
        case = by_feast.get(row["feast"])
        if case is None:
            raise ValueError(f"No labelled case for {row['feast']!r}")
        failed = row.get("verdict") == "error" or "arm_failed" in row.get("diagnostics", ())
        status, matches = row.get("status"), row.get("matches") or []
        rescored.append({**row, **score(case, status, matches, failed=failed)})
    missing = sorted(set(by_feast) - {row["feast"] for row in rows})
    summary = summarise(rescored)
    summary["unscored_cases"] = missing
    if isinstance(report, dict) and "profile" in report:
        summary = {"profile": report["profile"], **summary}
    return summary


class Command(BaseCommand):
    help = (
        "Score one registered profile against labelled cases (--live, billable), "
        "or re-score a saved report against the current labels (--rescore-json, offline)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--cases-json", required=True)
        parser.add_argument("--output-json", required=True)
        parser.add_argument("--catalogue-json")
        parser.add_argument("--profile", choices=tuple(REGISTERED_PROFILES))
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--rescore-json")
        parser.add_argument("--arm-timeout", type=float)

    def handle(self, *args, **options):
        if options["live"] == bool(options["rescore_json"]):
            raise CommandError("Pass exactly one of --live (billable) or --rescore-json REPORT (offline)")
        if options["live"] and not (options["catalogue_json"] and options["profile"]):
            raise CommandError("--live requires --catalogue-json and --profile")
        try:
            output = Path(options["output_json"])
            inputs = {
                Path(options[k]).resolve() for k in ("catalogue_json", "cases_json", "rescore_json") if options[k]
            }
            if output.resolve() in inputs:
                raise ValueError("Output must not overwrite an input")
            cases = json.loads(Path(options["cases_json"]).read_text())
            if not isinstance(cases, list):
                raise ValueError("Cases must be an array")
            if options["live"]:
                catalogue = json.loads(Path(options["catalogue_json"]).read_text())
                if not isinstance(catalogue, list):
                    raise ValueError("Catalogue must be an array")
                report = evaluate_cases(
                    catalogue, cases, REGISTERED_PROFILES[options["profile"]], arm_timeout=options["arm_timeout"]
                )
            else:
                report = rescore(json.loads(Path(options["rescore_json"]).read_text()), cases)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError(f"Invalid case evaluation inputs: {exc}") from exc
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k != "cases"}))
