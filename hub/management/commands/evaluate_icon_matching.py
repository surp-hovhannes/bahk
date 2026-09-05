"""Read-only JSON evaluation through the production matching orchestration.

Offline mode requires recorded stage responses (one list per request), or records
provider-unavailable outcomes without inventing recommendations. --live explicitly
enables the configured production provider. No assignment tasks or ORM writes.
"""

import json
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from hub.services.icon_matching import IconMatchRequest, generate_icon_candidates
from hub.services.icon_match_service import match_icons


class ReplayProvider:
    model = "offline-replay"

    def __init__(self, responses):
        self.responses = iter(responses)

    def call(self, stage, payload, schema, timeout):
        record = next(self.responses)
        if record["stage"] != stage:
            raise ValueError("replay_stage_mismatch")
        return record["response"]


class OfflineUnavailableProvider:
    model = "offline-no-provider"

    def call(self, stage, payload, schema, timeout):
        raise RuntimeError("offline_no_recorded_response")


def evaluate(catalogue, requests, *, live=False, replays=None, provider_factory=None, deterministic_baseline=False):
    """Also callable with an injected fixture provider; never performs ORM lookup."""
    started = time.monotonic()
    results = []
    for index, item in enumerate(requests):
        if isinstance(item, str):
            item = {"primary_text": item, "kind": "feast"}
        request = IconMatchRequest(
            kind=item.get("kind", "content"),
            primary_text=item.get("primary_text", item.get("title", item.get("name", item.get("text", "")))),
            context_terms=tuple(item.get("context_terms", item.get("tags", []))),
            auto_assign_policy="feast_strict" if item.get("kind") == "feast" else "content_suggest",
            max_results=item.get("max_results", 10),
        )
        provider = (
            provider_factory(index)
            if provider_factory
            else ReplayProvider(replays[index])
            if replays is not None
            else None
            if live
            else OfflineUnavailableProvider()
        )
        outcome = match_icons(catalogue, request, provider=provider)
        baseline = {}
        if deterministic_baseline:
            icons = [
                SimpleNamespace(id=r["id"], title=r["title"], tags=r.get("tags", r.get("tag_list", [])))
                for r in catalogue
            ]
            candidates = generate_icon_candidates(icons, request)
            baseline = {
                "deterministic_baseline": {
                    "any": bool(candidates),
                    "direct": any(c.match_tier == "direct_exact" for c in candidates),
                    "candidate_ids": [c.icon_id for c in candidates],
                }
            }
        results.append(
            {
                **baseline,
                "request_index": index,
                "kind": request.kind,
                "primary_text": request.primary_text,
                "outcome": outcome.to_dict(),
            }
        )
    return {
        "mode": "live" if live else "offline",
        "label": "Exploratory coverage audit; no accuracy labels supplied",
        "catalogue_count": len(catalogue),
        "request_count": len(requests),
        "status_counts": dict(Counter(r["outcome"]["status"] for r in results)),
        "recommendation_count": sum(bool(r["outcome"]["matches"]) for r in results),
        "auto_assignment_count": sum(any(m["auto_assignable"] for m in r["outcome"]["matches"]) for r in results),
        "deterministic_baseline_counts": {
            key: sum(r.get("deterministic_baseline", {}).get(key, False) for r in results) for key in ("any", "direct")
        }
        if deterministic_baseline
        else None,
        "call_count": sum(r["outcome"]["call_count"] for r in results),
        "matching_elapsed_seconds": round(sum(r["outcome"]["elapsed_seconds"] for r in results), 3),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "wire_call_count": sum(r["outcome"]["wire_call_count"] for r in results),
        "results": results,
    }


class Command(BaseCommand):
    help = "Evaluate supplied icon/request JSON without ORM assignment. Provider calls require --live."

    def add_arguments(self, parser):
        parser.add_argument("--catalogue-json", required=True)
        parser.add_argument("--requests-json", required=True)
        parser.add_argument("--output-json", required=True)
        parser.add_argument("--responses-json", help="Offline stage response lists, indexed by request")
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--deterministic-baseline", action="store_true")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        try:
            if options["live"] and options["responses_json"]:
                raise ValueError("--live and --responses-json are mutually exclusive")
            input_paths = [options["catalogue_json"], options["requests_json"], options["responses_json"]]
            if Path(options["output_json"]).resolve() in {Path(p).resolve() for p in input_paths if p}:
                raise ValueError("Output must not overwrite an input")
            catalogue = json.loads(Path(options["catalogue_json"]).read_text())
            requests = json.loads(Path(options["requests_json"]).read_text())
            if not isinstance(catalogue, list) or not isinstance(requests, list):
                raise ValueError("Catalogue and requests must be JSON arrays")
            if options["limit"] is not None:
                if options["limit"] < 1:
                    raise ValueError("--limit must be positive")
                requests = requests[: options["limit"]]
            replays = json.loads(Path(options["responses_json"]).read_text()) if options["responses_json"] else None
            if replays is not None and len(replays) < len(requests):
                raise ValueError("Missing replay responses")
            report = evaluate(
                catalogue,
                requests,
                live=options["live"],
                replays=replays,
                deterministic_baseline=options["deterministic_baseline"],
            )
            Path(options["output_json"]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError("Invalid evaluation inputs or output path") from exc
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k != "results"}))
