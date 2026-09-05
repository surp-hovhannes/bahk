"""Evaluation-only paired replay/live audit, with a shared dispatch budget."""

import json
import math
import time
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from hub.management.commands.evaluate_icon_matching import OfflineUnavailableProvider
from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_profiles import DEFAULT_PROFILES, REGISTERED_PROFILES, digest
from hub.services.icon_match_service import (
    IconMatchOutcome,
    MatchLimits,
    OpenAIIconProvider,
    match_icons,
    serialize_catalogue,
    provider_payload,
    wire_schema,
    normalize_provider_matches,
    _size,
)


class WireBudget:
    """Shared by both arms and every retry/repair; consumed at actual dispatch."""

    def __init__(self, maximum):
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("Live evaluation requires a positive maximum wire-call budget")
        self.maximum = maximum
        self.used = 0
        self.denied = 0
        self._lock = Lock()

    def consume(self):
        with self._lock:
            if self.used >= self.maximum:
                self.denied += 1
                raise RuntimeError("wire_budget_exhausted")
            self.used += 1


class ReplayHTTPError(Exception):
    def __init__(self, status):
        super().__init__("recorded_http_failure")
        self.status_code = status
        # Replay exercises retry decisions without reproducing historical backoff.
        self.response = SimpleNamespace(headers={"retry-after": "0"})


def replay_binding(stage, payload, schema, profile):
    return {
        "stage": stage,
        "payload_hash": digest(provider_payload(payload)),
        "schema_hash": digest(wire_schema(schema)),
        "profile_hash": profile.metadata()["profile_hash"],
    }


class PairedReplayProvider:
    """Strictly bound wire responses; historical usage is not new wire usage."""

    model = "offline-replay"
    wire_call_count = 0

    def __init__(self, responses, profile):
        self.responses = iter(deepcopy(responses))
        self.profile = profile
        self.model_ids = set()
        self.usage_records = []
        self.diagnostics = []

    def call(self, stage, payload, schema, timeout):
        record = next(self.responses)
        if any(record.get(key) != value for key, value in replay_binding(stage, payload, schema, self.profile).items()):
            self.diagnostics.append("replay_binding_mismatch")
            raise ValueError("replay_binding_mismatch")
        if isinstance(record.get("model"), str):
            self.model_ids.add(record["model"])
        self.usage_records.append(record.get("usage"))
        if record.get("error") is not None:
            error = record["error"]
            if not isinstance(error, dict):
                raise ValueError("invalid_recorded_error")
            if error.get("category") == "timeout":
                raise TimeoutError("recorded_timeout")
            if (
                error.get("category") == "http"
                and type(error.get("status")) is int
                and error["status"] in (400, 401, 403, 404, 408, 422, 429, 500, 502, 503, 504)
            ):
                raise ReplayHTTPError(error["status"])
            if error.get("category") == "invalid_response":
                raise ValueError("recorded_invalid_response")
            raise ValueError("invalid_recorded_error")
        result = deepcopy(record["response"])
        return normalize_provider_matches(result, schema, payload["catalogue"]) if stage != "analyze" else result


def usage_summary(records):
    """Top-level totals only; details are subsets, retained per call, never added."""
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    totals = {
        key: sum(record[key] for record in records)
        if records and all(isinstance(record, dict) and type(record.get(key)) is int for record in records)
        else None
        for key in fields
    }
    return {
        "per_call": records,
        "totals": totals,
        "complete": bool(records) and all(value is not None for value in totals.values()),
    }


class InvalidRequest(ValueError):
    """Safe, actionable preflight errors without echoing request contents."""


def canonical_request(item):
    if isinstance(item, str):
        item = {"kind": "feast", "primary_text": item}
    if not isinstance(item, dict):
        raise InvalidRequest("expected a string or object")
    kind = item.get("kind", "content")
    primary_text = item.get("primary_text", item.get("title", item.get("name", item.get("text", ""))))
    context_terms = item.get("context_terms", item.get("tags", []))
    max_results = item.get("max_results", 10)
    if kind not in ("feast", "content"):
        raise InvalidRequest("kind must be feast or content")
    if not isinstance(primary_text, str) or not primary_text.strip():
        raise InvalidRequest("primary text must be a nonempty string")
    if not isinstance(context_terms, (list, tuple)) or any(not isinstance(term, str) for term in context_terms):
        raise InvalidRequest("context_terms/tags must be an array of strings (not null)")
    if len(context_terms) > 32:
        raise InvalidRequest("context_terms/tags must contain at most 32 strings")
    if type(max_results) is not int or not 1 <= max_results <= 100:
        raise InvalidRequest("max_results must be an integer between 1 and 100")
    request = IconMatchRequest(
        kind=kind,
        primary_text=primary_text,
        context_terms=tuple(context_terms),
        auto_assign_policy="feast_strict" if kind == "feast" else "content_suggest",
        max_results=max_results,
    )
    if _size(asdict(request)) > 16000:
        raise InvalidRequest("serialized request must not exceed 16000 bytes")
    return request


def prepare_requests(requests, records):
    """Finish canonicalization and digest serialization before either arm can run."""
    if not isinstance(requests, (list, tuple)):
        raise InvalidRequest("Requests must be an array")
    prepared = []
    for index, item in enumerate(requests):
        try:
            request = canonical_request(item)
            input_digest = digest({"catalogue": records, "request": asdict(request)})
        except InvalidRequest as exc:
            raise InvalidRequest(f"Invalid request at index {index}: {exc}") from exc
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidRequest(
                f"Invalid request at index {index}: cannot serialize canonical input as UTF-8 JSON"
            ) from exc
        prepared.append((request, input_digest))
    return prepared


class InvalidProfiles(ValueError):
    """Profile selection failed before provider construction or spending."""


def select_profiles(profiles):
    if not isinstance(profiles, (tuple, list)) or len(profiles) != 2:
        raise InvalidProfiles("Select exactly two distinct registered profiles")
    selected = []
    for value in profiles:
        if isinstance(value, str):
            profile = REGISTERED_PROFILES.get(value)
        else:
            profile = next((p for p in REGISTERED_PROFILES.values() if value is p), None)
        if profile is None:
            raise InvalidProfiles("Unknown or unregistered profile")
        selected.append(profile)
    if selected[0] is selected[1]:
        raise InvalidProfiles("Select exactly two distinct registered profiles")
    return tuple(selected)


def evaluate_paired(
    catalogue,
    requests,
    *,
    live=False,
    maximum_wire_calls=None,
    replays=None,
    arm_timeout=180,
    profiles=DEFAULT_PROFILES,
):
    profiles = select_profiles(profiles)
    if not math.isfinite(arm_timeout) or arm_timeout <= 0:
        raise ValueError("Arm timeout must be positive and finite")
    if live and replays is not None:
        raise ValueError("Live and replay are mutually exclusive")
    budget = WireBudget(maximum_wire_calls) if live else None
    records = serialize_catalogue(catalogue)
    catalogue_digest = digest(records)
    prepared = prepare_requests(requests, records)
    pairs = []
    for index, (request, input_digest) in enumerate(prepared):
        case_id = f"case-{index:04d}-{input_digest[:12]}"
        order = profiles if index % 2 == 0 else profiles[::-1]
        pair = {
            "case_id": case_id,
            "request_index": index,
            "request": asdict(request),
            "input_digest": input_digest,
            "catalogue_digest": catalogue_digest,
            "arm_order": [p.id for p in order],
            "arms": {},
        }
        for profile in order:
            limits = replace(MatchLimits(), positive_limit=profile.positive_limit, total_seconds=arm_timeout)
            started = time.monotonic()
            denied_before = budget.denied if budget else 0
            provider = None
            state = "evaluated"
            outcome = IconMatchOutcome(catalogue_count=len(records))
            if budget and budget.used >= budget.maximum:
                state = "skipped_budget"
                outcome.diagnostics = ["wire_budget_exhausted"]
            else:
                try:
                    if live:
                        provider = OpenAIIconProvider(
                            model=profile.model,
                            profile=profile,
                            reasoning_effort=profile.reasoning_effort,
                            wire_budget=budget,
                        )
                    elif replays is not None:
                        provider = PairedReplayProvider(replays[case_id][profile.id], profile)
                    else:
                        provider = OfflineUnavailableProvider()
                    outcome = match_icons(
                        deepcopy(records), deepcopy(request), provider=provider, limits=limits, profile=profile
                    )
                    if budget and budget.denied > denied_before:
                        state = "partial_budget" if provider.wire_call_count else "skipped_budget"
                        outcome.diagnostics = sorted(set(outcome.diagnostics + ["wire_budget_exhausted"]))
                    elif outcome.status != "complete":
                        state = "failed" if outcome.status == "unavailable" else "partial"
                except Exception:
                    # Preserve the pair, never raw provider errors/credentials.
                    state = "failed"
                    outcome.diagnostics = ["arm_initialization_or_replay_failed"]
            pair["arms"][profile.id] = {
                **profile.metadata(),
                "state": state,
                "limits": asdict(limits),
                "input_digest": input_digest,
                "catalogue_digest": catalogue_digest,
                "returned_models": sorted(getattr(provider, "model_ids", [])),
                "provider_model": getattr(provider, "model", None),
                "replay_diagnostics": getattr(provider, "diagnostics", []),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "wire_calls": getattr(provider, "wire_call_count", 0),
                "usage": usage_summary(deepcopy(getattr(provider, "usage_records", []))),
                "usage_origin": "live_dispatch" if live else "recorded_response",
                "outcome": outcome.to_dict(),
            }
        pairs.append(pair)
    summary = {}
    for profile in profiles:
        arms = [pair["arms"][profile.id] for pair in pairs]
        complete = [arm for arm in arms if arm["state"] == "evaluated" and arm["outcome"]["status"] == "complete"]
        summary[profile.id] = {
            "state_counts": dict(Counter(arm["state"] for arm in arms)),
            "complete_count": len(complete),
            "complete_no_match_count": sum(not arm["outcome"]["matches"] for arm in complete),
            "recommendation_count": sum(bool(arm["outcome"]["matches"]) for arm in arms),
            "eligible_count": sum(any(m["auto_assignable"] for m in arm["outcome"]["matches"]) for arm in arms),
        }
    completed_pairs = [
        pair
        for pair in pairs
        if all(arm["state"] == "evaluated" and arm["outcome"]["status"] == "complete" for arm in pair["arms"].values())
    ]
    both_recommended = [
        pair for pair in completed_pairs if all(arm["outcome"]["matches"] for arm in pair["arms"].values())
    ]
    return {
        "mode": "live" if live else "offline",
        "label": "Exploratory paired audit; no accuracy labels or winner",
        "selected_profile_ids": [p.id for p in profiles],
        "comparison": (
            "Prompt-only comparison; same model, reasoning, limits and recommendation policy"
            if {p.id for p in profiles} == {"luna-v1", "luna-v2"}
            else "Bundled model, prompt and recommendation policy profiles; not model-only effects"
        ),
        "catalogue_digest": catalogue_digest,
        "catalogue_count": len(records),
        "request_count": len(requests),
        "maximum_wire_calls": maximum_wire_calls if live else None,
        "wire_calls": budget.used if budget else 0,
        "usage": usage_summary(
            [record for pair in pairs for arm in pair["arms"].values() for record in arm["usage"]["per_call"]]
        ),
        "usage_origin": "live_dispatch" if live else "recorded_response",
        "summary": summary,
        "complete_pair_count": len(completed_pairs),
        "both_recommended_pair_count": len(both_recommended),
        "top_result_agreement_count": sum(
            len({arm["outcome"]["matches"][0]["id"] for arm in pair["arms"].values()}) == 1 for pair in both_recommended
        ),
        "pairs": pairs,
    }


class Command(BaseCommand):
    help = "Read-only paired profile audit. Billable calls require --live AND --maximum-wire-calls."

    def add_arguments(self, parser):
        for name in ("catalogue-json", "requests-json", "output-json"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument(
            "--responses-json", help="Offline responses keyed by stable case_id, then selected profile ID"
        )
        parser.add_argument(
            "--profiles", nargs=2, choices=tuple(REGISTERED_PROFILES), default=[p.id for p in DEFAULT_PROFILES]
        )
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--maximum-wire-calls", type=int)
        parser.add_argument("--arm-timeout", type=float, default=180)
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        try:
            inputs = [options[k] for k in ("catalogue_json", "requests_json", "responses_json") if options[k]]
            output = Path(options["output_json"])
            if output.resolve() in {Path(p).resolve() for p in inputs}:
                raise ValueError("Output must not overwrite an input")
            catalogue = json.loads(Path(options["catalogue_json"]).read_text())
            requests = json.loads(Path(options["requests_json"]).read_text())
            if not isinstance(catalogue, list) or not isinstance(requests, list):
                raise ValueError("Catalogue and requests must be arrays")
            if options["limit"] is not None:
                if options["limit"] <= 0:
                    raise ValueError("Limit must be positive")
                requests = requests[: options["limit"]]
            replays = json.loads(Path(options["responses_json"]).read_text()) if options["responses_json"] else None
            report = evaluate_paired(
                catalogue,
                requests,
                live=options["live"],
                maximum_wire_calls=options["maximum_wire_calls"],
                replays=replays,
                arm_timeout=options["arm_timeout"],
                profiles=options["profiles"],
            )
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except (InvalidRequest, InvalidProfiles) as exc:
            raise CommandError(str(exc)) from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError(
                "Invalid paired evaluation inputs; live requires a positive --maximum-wire-calls"
            ) from exc
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k != "pairs"}))
