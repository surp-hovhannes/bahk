"""Sanitized provider responses, deliberately independent of production vocabulary."""

from copy import deepcopy


def analysis_for(subject="Սուրբ Նարեկ", event=None):
    return {
        "intent": "event" if event else ("subject" if subject else "theme"),
        "subjects": [{"span": {"ref": "request:primary", "quote": subject}, "qualifiers": []}] if subject else [],
        "event": [{"ref": "request:primary", "quote": event}] if event else [],
        "context": [],
        "unresolved": [],
    }


def candidate(icon_id, title, relation="exact_subject", **overrides):
    result = {
        "id": icon_id,
        "relation": relation,
        "confidence": "high",
        "relevance": 75,
        "reason": f"The title identifies {title}.",
        "evidence": [
            {
                "ref": f"icon:{icon_id}:title",
                "quote": title,
                "role": "identity",
                "subject_indices": overrides.get("covered_subjects", [0]),
            }
        ],
        "covered_subjects": [0],
        "full_request_coverage": True,
        "identity_qualified": True,
        "event_agrees": True,
        "generic_portrait": relation == "subject_portrait",
        "conflict": False,
    }
    if relation == "exact_event":
        result["evidence"].append(
            {"ref": f"icon:{icon_id}:title", "quote": title, "role": "event", "subject_indices": []}
        )
    result.update(overrides)
    for evidence in result["evidence"]:
        evidence.setdefault("subject_indices", [])
    return result


class FixtureProvider:
    model = "offline-fixture"

    def __init__(self, analysis, matches=(), mutate=None):
        self.analysis = analysis
        self.matches = list(matches)
        self.mutate = mutate
        self.calls = []

    def call(self, stage, payload, schema, timeout):
        self.calls.append((stage, deepcopy(payload), timeout))
        if stage == "analyze":
            result = deepcopy(self.analysis)
        elif stage == "assess":
            ids = [r["id"] for r in payload["catalogue"]]
            matches = [deepcopy(m) for m in self.matches if m["id"] in ids]
            # Fixtures implement the contract's relation priority before top-N.
            order = ["exact_event", "exact_subject", "subject_portrait", "related_specific", "thematic"]
            matches.sort(
                key=lambda m: (
                    order.index(m["relation"]),
                    -len(m["covered_subjects"]),
                    ["high", "medium", "low"].index(m["confidence"]),
                    -m["relevance"],
                    m["id"],
                )
            )
            result = {
                "batch_id": payload["batch_id"],
                "assessment_complete": True,
                "matches": matches[: payload["positive_limit"]],
                "exact_event_exists": any(m["relation"] == "exact_event" for m in matches),
            }
        else:
            result = {
                "analysis": deepcopy(self.analysis),
                "request_coverage_complete": True,
                "reviewed_ids": [r["id"] for r in payload["catalogue"]],
                "matches": deepcopy(payload["candidates"]),
                "truncated": False,
            }
        if self.mutate:
            self.mutate(stage, payload, result)
        return result
