#!/usr/bin/env python3
"""Private historical-response replay in disposable SQLite, never a provider evaluation.

Usage: .venv/bin/python scripts/replay-icon-taxonomy.py INPUT.json --output /private/tmp/report.json
The input and output may contain private catalogue labels; never publish them.
"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DJANGO_SETTINGS_MODULE"] = "tests.test_settings"
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/bahk-mpl-cache")
os.environ.setdefault("AWS_CONFIG_FILE", "/dev/null")
os.environ.setdefault("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import django

django.setup()

from django.conf import settings
from django.core.management import call_command
from icons.models import TaxonomyConcept
from icons.services.taxonomy_evidence import normalize_comparison
from icons.services.taxonomy_inputs import analysis_dependencies, dependencies_current, normalize, versions
from icons.services.taxonomy_rules import validate_assertions
from icons.services.taxonomy_vocabulary import catalogue_claims, catalogue_sources, parse, seed_vocabulary, EVENT
from icons.services.vision_provider import VisionProvider


def saved_labels(rows):
    """Never infer a foreign ID by position or the current database's integer IDs.

    Labels come from saved assertion labels with direct claim references, or a
    saved singleton source span whose parsed kind/ID is explicit. Composite
    subjects without an individual labelled source remain unmapped.
    """
    labels = defaultdict(set)
    origins = defaultdict(list)

    def add(pk, kind, label, origin):
        labels[pk].add((kind, normalize(label)))
        origins[pk].append(origin)

    for row in rows:
        analysis = row["analysis"]
        for assertion in analysis.get("assertions", []):
            if assertion.get("concept__label"):
                for evidence in assertion.get("evidence", []):
                    if evidence.get("concept") is not None and evidence.get("source") in {"title", "tag", "filename"}:
                        add(
                            evidence["concept"],
                            assertion["concept__kind"],
                            assertion["concept__label"],
                            {"icon": row["id"], "basis": "saved_assertion_label"},
                        )
        for source in analysis["claims"].get("sources", []):
            parsed = source["parsed"]
            text = source["parse_text"]
            for kind in ("event", "group"):
                if parsed.get(kind):
                    add(parsed[kind], kind, text, {"icon": row["id"], "basis": "saved_source_" + kind})
            subjects = parsed.get("subjects", [])
            if len(subjects) == 1 and not parsed.get("group"):
                event = EVENT.fullmatch(normalize(text))
                label = event[2] if event else text
                if not parsed.get("event") or event:
                    add(subjects[0], "subject", label, {"icon": row["id"], "basis": "saved_singleton_subject_span"})
    return labels, origins


def label_mapping(rows):
    labels, origins = saved_labels(rows)
    mapping = {}
    for old, options in sorted(labels.items()):
        targets = set()
        all_resolved = True
        for kind, label in options:
            parsed = parse(label)
            candidates = (
                parsed["subjects"]
                if kind == "subject" and not parsed["event_intent"]
                else (parsed["themes"] if kind == "theme" else [parsed.get(kind)] if kind in {"event", "group"} else [])
            )
            resolved = {pk for pk in candidates if pk is not None and TaxonomyConcept.objects.get(pk=pk).kind == kind}
            all_resolved &= len(resolved) == 1
            targets.update(resolved)
        # Every saved labelled alternative must resolve to the same meaning;
        # conflicting evidence is not a license to choose the convenient label.
        target = next(iter(targets)) if all_resolved and len(targets) == 1 else None
        mapping[old] = {
            "saved_labels": [dict(kind=k, label=v) for k, v in sorted(options)],
            "label_sources": origins[old],
            "new_concept": target,
            "new_label": TaxonomyConcept.objects.get(pk=target).label if target else None,
            "status": "mapped_from_saved_label" if target else "unresolved_or_removed",
        }
    return mapping


def counts(assertions):
    return {
        "status": {
            status: sum(a["status"] == status for a in assertions)
            for status in ("supported", "unknown", "contradicted")
        },
        "supported": dict(
            Counter(a["attribute"] + ":" + a["evidence_level"] for a in assertions if a["status"] == "supported")
        ),
        "nominal_nonportrait_support": sum(
            a["status"] == "supported" and a["attribute"] != "portrait" for a in assertions
        ),
    }


def replay(rows):
    seed_vocabulary()
    # Stabilize the bounded cohort vocabulary before mapping old labels, then
    # also check incremental processing below to expose dependency growth.
    for row in rows:
        catalogue_claims(row["analysis"]["claims"]["metadata"])
    mapping = label_mapping(rows)
    output, snapshots = [], {}
    for row in rows:
        old = row["analysis"]
        metadata = old["claims"]["metadata"]
        claims = catalogue_claims(metadata)
        sources = catalogue_sources(metadata)
        response = deepcopy(old["comparison"])
        remaps = []
        old_claims = {c["concept"] for c in old["claims"]["claims"]}
        for entry in response["assertions"]:
            pk = entry["concept"]
            target = mapping.get(pk, {}).get("new_concept") if pk in old_claims else None
            # Negative sentinels cannot collide with ephemeral positive DB IDs.
            entry["concept"] = target if target is not None else -(abs(pk) + 1)
            remaps.append(
                {
                    "old_concept": pk,
                    "new_concept": target,
                    "reason": mapping.get(pk, {}).get("status", "unknown_saved_id_no_mapping")
                    if pk in old_claims
                    else "not_supplied_to_historical_call",
                }
            )
        accepted, conflicts, diagnostics = normalize_comparison(claims, old["observation"], response)
        assertions = validate_assertions(claims, old["observation"], response, sources=sources)
        snapshot = analysis_dependencies(metadata, claims, old["observation"])
        snapshots[row["id"]] = snapshot
        old_claims = {c["concept"] for c in old["claims"]["claims"]}
        old_refs = Counter(e["concept"] for e in old["comparison"]["assertions"])
        old_invalid = {
            "foreign_entries": sum(e["concept"] not in old_claims for e in old["comparison"]["assertions"]),
            "duplicate_entries": sum(n - 1 for n in old_refs.values() if n > 1),
            "unknown_observation_entries": sum(
                not set(e["observation_ids"]) <= {o["id"] for o in old["observation"]["observations"]}
                for e in old["comparison"]["assertions"]
            ),
        }
        output.append(
            {
                "id": row["id"],
                "title": row["title"],
                "historical_state": old["state"],
                "historical_projection_current": row.get("projection_current"),
                "historical_freshness_cause": "not_exported_cannot_determine"
                if row.get("projection_current") is False and old["state"] == "complete"
                else None,
                "old": counts(old["assertions"]),
                "new": counts(assertions),
                "old_invalid": old_invalid,
                "comparison_diagnostics": diagnostics,
                "accepted_comparison_entries": len(accepted),
                "conflicting_claims": sorted(conflicts),
                "reference_mapping": remaps,
                "historical_comparison": old["comparison"],
                "remapped_comparison": response,
                "replay_outcome": (
                    "uncertain"
                    if not any(a["status"] == "supported" for a in assertions)
                    else "partially_usable"
                    if any(d["code"] != "accepted_comparison_entry" for d in diagnostics)
                    else "complete"
                ),
                "assertions": [
                    {**a, "label": TaxonomyConcept.objects.get(pk=a["concept"]).label if a["concept"] else None}
                    for a in assertions
                ],
            }
        )
    for row in output:
        row["replay_dependencies_current_after_cohort"] = dependencies_current(snapshots[row["id"]])
    return mapping, output


def matching_probe(source_rows, replay_rows):
    """Materialize only disposable projections; no image/storage/provider access."""
    from hub.models import Church
    from hub.services.icon_matching import IconMatchRequest
    from hub.services.icon_taxonomy_matching import match_icons, projection_current
    from icons.models import Icon, IconAnalysis, IconTaxonomyProjection, IconTaxonomyWork
    from icons.services.taxonomy_inputs import fingerprint

    sources = {r["id"]: r for r in source_rows}
    icons = []
    for row in replay_rows:
        data = sources[row["id"]]["analysis"]["claims"]["metadata"]
        Church.objects.get_or_create(pk=data["church"], defaults={"name": "Ephemeral replay church"})
        icon = Icon(
            pk=row["id"],
            title=data["title"],
            church_id=data["church"],
            image=data["image"],
            original_filename=data["filename"],
            filename_provenance=data["filename_provenance"],
            image_content_digest=data["image_digest"],
            image_revision=data["image_revision"],
        )
        Icon.objects.bulk_create([icon])  # bypass storage/ingestion, disposable DB only
        icon.tags.add(*data["tags"])
        claims = catalogue_claims(data)
        deps = analysis_dependencies(data, claims, sources[row["id"]]["analysis"]["observation"])
        work, _ = IconTaxonomyWork.objects.update_or_create(
            icon=icon, defaults={"state": "complete", "fingerprint": fingerprint(icon)}
        )
        analysis = IconAnalysis.objects.create(
            icon=icon,
            church_id=icon.church_id,
            digest=str(row["id"]).zfill(64),
            image_digest=data["image_digest"],
            versions=versions(),
            dependencies=deps,
            claims={"metadata": data},
            state="complete",
        )
        attributes = [{k: v for k, v in a.items() if k != "label"} for a in row["assertions"]]
        projection = IconTaxonomyProjection.objects.create(
            icon=icon,
            church_id=icon.church_id,
            analysis=analysis,
            revision=work.revision,
            fingerprint=work.fingerprint,
            attributes=attributes,
        )
        row["ephemeral_projection_current"] = projection_current(icon, projection)
        icons.append(icon)
    for row in replay_rows:
        source = sources[row["id"]]
        outcome = match_icons(
            icons,
            IconMatchRequest(
                kind="content", primary_text=source["title"], auto_assign_policy="content_suggest", max_results=50
            ),
            church_id=source["analysis"]["claims"]["metadata"]["church"],
            allow_adapter=False,
        )
        row["title_request_matching"] = {
            "suggestions": len(outcome.matches),
            "own_icon_retrieved": any(m["id"] == row["id"] for m in outcome.matches),
            "auto_assignable": sum(m["auto_assignable"] for m in outcome.matches),
            "diagnostics": outcome.diagnostics,
        }
    themes = {}
    for theme in ("prayer", "service", "humility", "repentance", "gratitude"):
        outcome = match_icons(
            icons,
            IconMatchRequest(kind="content", primary_text=theme, auto_assign_policy="content_suggest", max_results=50),
            allow_adapter=False,
        )
        themes[theme] = {
            "suggestions": len(outcome.matches),
            "auto_assignable": sum(m["auto_assignable"] for m in outcome.matches),
        }
    return themes


def growth_probe(rows):
    seed_vocabulary()
    snapshots = {}
    for row in rows:
        data = row["analysis"]["claims"]["metadata"]
        claims = catalogue_claims(data)
        snapshots[row["id"]] = analysis_dependencies(data, claims, row["analysis"]["observation"])
    stale = [pk for pk, snapshot in snapshots.items() if not dependencies_current(snapshot)]
    # Refresh only dependency snapshots from retained evidence, without a new
    # observation/provider call, after the full selected vocabulary is present.
    for row in rows:
        if row["id"] in stale:
            data = row["analysis"]["claims"]["metadata"]
            snapshots[row["id"]] = analysis_dependencies(data, catalogue_claims(data), row["analysis"]["observation"])
    return {
        "stale_after_incremental_cohort": stale,
        "current_after_retained_evidence_refresh": sum(dependencies_current(s) for s in snapshots.values()),
    }


def aggregate(rows):
    result = {"icons": len(rows)}
    for side in ("old", "new"):
        result[side] = {
            "status": {
                status: sum(r[side]["status"].get(status, 0) for r in rows)
                for status in ("supported", "unknown", "contradicted")
            },
            "supported": dict(sum((Counter(r[side]["supported"]) for r in rows), Counter())),
            "icons_with_nominal_nonportrait_support": sum(bool(r[side]["nominal_nonportrait_support"]) for r in rows),
        }
    result["historical_failure_kinds"] = dict(
        Counter(
            "both"
            if r["old_invalid"]["foreign_entries"] and r["old_invalid"]["duplicate_entries"]
            else "foreign_only"
            if r["old_invalid"]["foreign_entries"]
            else "duplicate_only"
            if r["old_invalid"]["duplicate_entries"]
            else "other"
            for r in rows
            if r["historical_state"] == "unavailable"
        )
    )
    result["formerly_unavailable_with_supported_attributes"] = sum(
        r["historical_state"] == "unavailable" and bool(r["new"]["status"].get("supported")) for r in rows
    )
    result["formerly_unavailable_with_nonportrait_support"] = sum(
        r["historical_state"] == "unavailable" and bool(r["new"]["nominal_nonportrait_support"]) for r in rows
    )
    result["ephemeral_current_projections"] = sum(r.get("ephemeral_projection_current", False) for r in rows)
    result["title_requests_retrieving_own_icon"] = sum(
        r.get("title_request_matching", {}).get("own_icon_retrieved", False) for r in rows
    )
    result["title_request_auto_assignable_suggestions"] = sum(
        r.get("title_request_matching", {}).get("auto_assignable", 0) for r in rows
    )
    result["historical_states"] = dict(Counter(r["historical_state"] for r in rows))
    result["replay_outcomes"] = dict(Counter(r["replay_outcome"] for r in rows))
    result["historical_invalid_entries"] = {
        key: sum(r["old_invalid"][key] for r in rows)
        for key in ("foreign_entries", "duplicate_entries", "unknown_observation_entries")
    }
    result["new_comparison_diagnostics"] = dict(Counter(d["code"] for r in rows for d in r["comparison_diagnostics"]))
    result["inferred_theme_basis"] = dict(
        Counter(
            "metadata_scene_only"
            if all(
                e["source"] == "sourced_event_relation" and e.get("event_evidence_level") == "metadata"
                for e in a["evidence"]
            )
            else "observed_activity_or_scene"
            for r in rows
            for a in r["assertions"]
            if a["attribute"] == "theme" and a["status"] == "supported"
        )
    )
    result["replay_current_dependencies"] = sum(r["replay_dependencies_current_after_cohort"] for r in rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.DATABASES["default"]["NAME"] == ":memory:"
    raw = args.input.read_bytes()
    rows = json.loads(raw)["icons"]
    with patch.object(VisionProvider, "call", side_effect=AssertionError("Offline replay forbids provider calls")):
        call_command("migrate", verbosity=0, interactive=False)
        mapping, results = replay(rows)
        theme_matching = matching_probe(rows, results)
        call_command("flush", verbosity=0, interactive=False)
        _, reversed_results = replay(list(reversed(rows)))
        call_command("flush", verbosity=0, interactive=False)
        forward_growth = growth_probe(rows)
        call_command("flush", verbosity=0, interactive=False)
        reverse_growth = growth_probe(list(reversed(rows)))
    by_id = {r["id"]: r for r in reversed_results}

    def semantic_assertions(row):
        return sorted(
            (a["attribute"], a["label"] or "", a["status"], a["evidence_level"], a["rule"]) for a in row["assertions"]
        )

    order_equal = all(semantic_assertions(row) == semantic_assertions(by_id[row["id"]]) for row in results)
    source_files = sorted(
        {
            *Path("icons/services").glob("taxonomy*.py"),
            Path("icons/services/ingestion.py"),
            Path("icons/services/vision_provider.py"),
            Path("hub/services/icon_taxonomy_matching.py"),
            *Path("icons/management/commands").glob("*taxonomy.py"),
            Path(__file__).relative_to(Path.cwd()),
        }
    )
    report = {
        "implementation_files": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files},
        "replay_provider_calls": 0,
        "kind": "historical_response_replay",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "versions": versions(),
        "summary": aggregate(results),
        "mapping": mapping,
        "cohort_reverse_order_equal": order_equal,
        "growth_forward": forward_growth,
        "growth_reverse": reverse_growth,
        "theme_matching": theme_matching,
        "rows": results,
        "limitations": [
            "No provider calls, new-model compliance, or independent real-image accuracy established.",
            "Saved metadata/observations are evidence, not ground truth.",
            "New claims absent from historical comparisons have metadata support only.",
            "Historical stale projection component values were not exported; replay cannot identify their cause.",
            "Replay projections/matching are disposable retained-evidence simulations, not production eligibility or visual ground truth.",
        ],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
