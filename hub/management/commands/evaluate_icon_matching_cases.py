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

from hub.services.clef import CLEF_MODELS
from hub.services.icon_clef_judge import (
    ASSIGN_THRESHOLD,
    RUNG_THRESHOLD,
    SHORTLIST_LIMIT,
    judge_icons,
    thumbnail_image_loader,
)
from hub.services.icon_match_profiles import REGISTERED_PROFILES
from hub.services.icon_match_service import MatchLimits, OpenAIIconProvider, match_icons
from hub.services.icon_matching import IconMatchRequest

# A case is {"feast": str, "correct": [id], "acceptable": [id], "assignable": [id]}.
# correct: depicts the commemoration. acceptable: a reasonable suggestion when
# nothing correct exists. assignable: may be written to the feast without a human;
# defaults to correct, so a fallback is only assignable when a reviewer says so.
# All empty means the catalogue holds nothing suitable and silence is right.
# A case marked "reviewed": false (see export_icon_match_cases) is not scored yet.
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


def run_cases(cases, match_case):
    """Score ``match_case(case) -> IconMatchOutcome`` over every case."""
    rows = []
    for case in cases:
        started = time.monotonic()
        try:
            outcome = match_case(case)
            row = {
                # Unavailable means nothing was assessed: an error, not a silence.
                **score(case, outcome.status, outcome.matches, failed=outcome.status == "unavailable"),
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
    return summarise(rows)


def llm_matcher(catalogue, profile, *, provider_factory=None, arm_timeout=None, max_results=5):
    """The production LLM matcher. Limits match the Celery feast path unless arm_timeout is given."""
    provider_factory = provider_factory or (lambda p: OpenAIIconProvider(profile=p))
    limits = replace(MatchLimits(), positive_limit=profile.positive_limit)
    if arm_timeout is not None:
        limits = replace(limits, total_seconds=arm_timeout)

    def match_case(case):
        return match_icons(
            catalogue,
            IconMatchRequest(
                kind="feast",
                primary_text=case["feast"],
                auto_assign_policy="feast_strict",
                max_results=case.get("max_results", max_results),
            ),
            provider=provider_factory(profile),
            limits=limits,
            profile=profile,
        )

    return match_case


def evaluate_cases(catalogue, cases, profile, *, provider_factory=None, arm_timeout=None):
    """Run every case through the LLM matcher with ``profile``."""
    match_case = llm_matcher(catalogue, profile, provider_factory=provider_factory, arm_timeout=arm_timeout)
    return {"profile": profile.metadata(), **run_cases(cases, match_case)}


def url_image_loader(catalogue, *, timeout=20):
    """``image_for`` for the Clef judge, from each catalogue record's ``thumbnail_url``."""
    return thumbnail_image_loader({record["id"]: record.get("thumbnail_url") for record in catalogue}, timeout=timeout)


def evaluate_cases_clef(
    catalogue, cases, *, model="clef", profile=None, provider_factory=None, arm_timeout=None, image_for=None
):
    """Run every case through the Clef judge.

    Without ``profile`` the shortlist is lexical and no LLM is called. With one,
    the LLM matcher's positives are the shortlist and Clef makes the decision;
    a shortlist from an incomplete LLM outcome keeps the outcome ``partial``.
    With ``image_for``, Clef also sees each shortlisted icon's image.
    """
    shortlister = None
    if profile is not None:
        shortlister = llm_matcher(
            catalogue,
            profile,
            provider_factory=provider_factory,
            arm_timeout=arm_timeout,
            max_results=SHORTLIST_LIMIT,
        )

    def match_case(case):
        if shortlister is None:
            return judge_icons(catalogue, case["feast"], model=model, image_for=image_for)
        shortlisted = shortlister(case)
        outcome = judge_icons(
            catalogue,
            case["feast"],
            candidate_ids=[m["id"] for m in shortlisted.matches],
            model=model,
            image_for=image_for,
        )
        outcome.diagnostics.append(f"llm_shortlist_status:{shortlisted.status}")
        if shortlisted.status != "complete" and outcome.status == "complete":
            outcome.status, outcome.catalogue_complete = "partial", False
        return outcome

    engine = {
        "engine": "llm+clef" if profile else "clef",
        "clef_model": model,
        "thresholds": {"rung": RUNG_THRESHOLD, "assign": ASSIGN_THRESHOLD},
        "images": image_for is not None,
        "shortlist": profile.metadata() if profile else {"source": "lexical", "limit": SHORTLIST_LIMIT},
    }
    return {"profile": engine, **run_cases(cases, match_case)}


def rescore(report, cases, skip=()):
    """Re-score a saved report (or a bare list of outcome rows) against current labels."""
    rows = [row for row in (report["cases"] if isinstance(report, dict) else report) if row["feast"] not in skip]
    by_feast = {case["feast"]: case for case in cases}
    rescored = []
    for row in rows:
        case = by_feast.get(row["feast"])
        if case is None:
            raise ValueError(f"No labelled case for {row['feast']!r}")
        failed = (
            row.get("verdict") == "error"
            or row.get("status") == "unavailable"
            or "arm_failed" in row.get("diagnostics", ())
        )
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
        "Score a matcher against labelled cases (--live, billable), "
        "or re-score a saved report against the current labels (--rescore-json, offline). "
        "--engine llm runs --profile; clef judges a lexical shortlist with Clef and calls no LLM; "
        "llm+clef judges --profile's positives with Clef."
    )

    def add_arguments(self, parser):
        parser.add_argument("--cases-json", required=True)
        parser.add_argument("--output-json", required=True)
        parser.add_argument("--catalogue-json")
        parser.add_argument("--engine", choices=("llm", "clef", "llm+clef"), default="llm")
        parser.add_argument("--profile", choices=tuple(REGISTERED_PROFILES))
        parser.add_argument("--clef-model", choices=CLEF_MODELS, default="clef")
        parser.add_argument(
            "--clef-images",
            action="store_true",
            help="Send each shortlisted icon's image to Clef, from the catalogue's thumbnail_url fields",
        )
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--rescore-json")
        parser.add_argument("--arm-timeout", type=float)

    def handle(self, *args, **options):
        if options["live"] == bool(options["rescore_json"]):
            raise CommandError("Pass exactly one of --live (billable) or --rescore-json REPORT (offline)")
        needs_profile = options["engine"] != "clef"
        if options["live"] and not options["catalogue_json"]:
            raise CommandError("--live requires --catalogue-json")
        if options["live"] and needs_profile != bool(options["profile"]):
            raise CommandError("--profile is required for --engine llm and llm+clef, and unused for clef")
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
            unreviewed = [case["feast"] for case in cases if case.get("reviewed") is False]
            cases = [case for case in cases if case.get("reviewed") is not False]
            if not cases:
                raise ValueError("No reviewed cases to score")
            if options["live"]:
                catalogue = json.loads(Path(options["catalogue_json"]).read_text())
                if not isinstance(catalogue, list):
                    raise ValueError("Catalogue must be an array")
                profile = REGISTERED_PROFILES[options["profile"]] if needs_profile else None
                if options["engine"] == "llm":
                    report = evaluate_cases(catalogue, cases, profile, arm_timeout=options["arm_timeout"])
                else:
                    report = evaluate_cases_clef(
                        catalogue,
                        cases,
                        model=options["clef_model"],
                        image_for=url_image_loader(catalogue) if options["clef_images"] else None,
                        profile=profile,
                        arm_timeout=options["arm_timeout"],
                    )
            else:
                report = rescore(json.loads(Path(options["rescore_json"]).read_text()), cases, skip=set(unreviewed))
            report["unreviewed_cases"] = unreviewed
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError(f"Invalid case evaluation inputs: {exc}") from exc
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k != "cases"}))
