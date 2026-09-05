"""Read-only deterministic matching over current private projections."""

from dataclasses import asdict
import time

from django.conf import settings

from hub.services.icon_match_service import IconMatchOutcome
from icons.models import Icon, IconTaxonomyProjection, TaxonomyAlias, TaxonomyConcept, TaxonomyRelation
from icons.services.taxonomy_inputs import dependencies_current, digest, fingerprint, normalize, versions
from icons.services.taxonomy_vocabulary import parse, NEGATION


def interpret(request, *, church_id=None, adapter=None, deadline=None, allow_adapter=True, commemoration=None):
    parsed = parse(request.primary_text)
    # Consume an exact bilingual pairing supplied by the calendar engine for this
    # request only; never enumerate/persist a calendar or manufacture aliases.
    if commemoration and request.primary_text in {
        commemoration.get("name"),
        commemoration.get("name_en"),
        commemoration.get("name_hy"),
    }:
        for label in (commemoration.get("name_en"), commemoration.get("name_hy")):
            if label and parsed["unresolved"]:
                alternative = parse(label)
                if not alternative["unresolved"]:
                    parsed = {**alternative, "paired_label": label}
    # Context tags are constraints too. Unknown spans are retained, never discarded.
    for text in request.context_terms:
        item = parse(text)
        parsed["subjects"] += item["subjects"]
        parsed["themes"] += item["themes"]
        parsed["unresolved"] += item["unresolved"]
        parsed["identity_constraint"] |= item["identity_constraint"]
        if item["group"]:
            if parsed["group"] and parsed["group"] != item["group"]:
                parsed["unresolved"].append(text)
            else:
                parsed["group"] = item["group"]
        if item["event_intent"]:
            if parsed["event_intent"] and parsed["event_intent"] != item["event_intent"]:
                parsed["unresolved"].append(text)
            else:
                parsed["event_intent"] = item["event_intent"]
        if item["event"]:
            if parsed["event"] and parsed["event"] != item["event"]:
                parsed["unresolved"].append(text)
            else:
                parsed["event"] = item["event"]
    parsed["subjects"] = sorted(set(parsed["subjects"]))
    parsed["themes"] = sorted(set(parsed["themes"]))
    parsed["adapter"] = False
    # Controlled theme lexical fallback provides suggestions for paraphrases, with
    # unresolved wording explicit. Never let a subject request become thematic.
    if (
        not parsed["identity_constraint"]
        and not parsed["subjects"]
        and not parsed["event_intent"]
        and not NEGATION.search(normalize(request.primary_text))
    ):
        text = " " + normalize(request.primary_text) + " "
        aliases = TaxonomyAlias.objects.filter(concept__kind="theme", concept__release__version=versions()["release"])
        parsed["themes"] = sorted(
            set(parsed["themes"]) | {a.concept_id for a in aliases if " " + a.normalized + " " in text}
        )
        if (
            allow_adapter
            and parsed["unresolved"]
            and (adapter or getattr(settings, "ICON_TAXONOMY_REQUEST_ADAPTER_ENABLED", False))
        ):
            from icons.services.request_adapter import adapt_request

            result = adapt_request(request, church_id=church_id, provider=adapter, deadline=deadline)
            parsed["themes"] = sorted(set(parsed["themes"]) | set(result["concepts"]))
            # Adapter never certifies complete understanding or eligibility.
            parsed["adapter"] = True
    return parsed


def projection_current(icon, projection, current_versions=None):
    if projection.church_id != icon.church_id or projection.fingerprint != fingerprint(icon):
        return False
    if projection.analysis.state != "complete" or projection.analysis.versions != (current_versions or versions()):
        return False
    if not dependencies_current(projection.analysis.dependencies):
        return False
    return icon.taxonomy_work.revision == projection.revision and icon.taxonomy_work.state == "complete"


def match_icons(icons, request, *, church_id=None, adapter=None, deadline=None, allow_adapter=True, commemoration=None):
    started = time.monotonic()
    allowed = {i["id"] if isinstance(i, dict) else i.pk for i in icons}
    qs = Icon.objects.filter(pk__in=allowed).prefetch_related("tags").select_related("taxonomy_work")
    if church_id is not None:
        qs = qs.filter(church_id=church_id)
    elif request.kind == "feast":
        return IconMatchOutcome(status="complete", diagnostics=["church_required"])
    icons = list(qs)
    parsed = interpret(
        request,
        church_id=church_id,
        adapter=adapter,
        deadline=deadline,
        allow_adapter=allow_adapter,
        commemoration=commemoration,
    )
    current_versions = versions()
    requested = set(parsed["subjects"])
    if parsed["group"] and not requested:
        requested.add(parsed["group"])
    themes = set(parsed["themes"])
    projections = {
        p.icon_id: p
        for p in IconTaxonomyProjection.objects.filter(icon_id__in=[i.pk for i in icons]).select_related("analysis")
    }
    outcome = IconMatchOutcome(
        status="complete",
        catalogue_count=len(icons),
        positives_complete=True,
        catalogue_digest=digest(
            {
                "scope": church_id,
                "request": asdict(request),
                "versions": current_versions,
                "revisions": [(p.icon_id, p.revision) for p in projections.values()],
            }
        ),
    )
    outcome.diagnostics = ["unresolved_request"] if parsed["unresolved"] else []
    matches = []
    for icon in icons:
        if deadline is not None and time.monotonic() >= deadline:
            outcome.diagnostics.append("deadline")
            break
        p = projections.get(icon.pk)
        if not p or not projection_current(icon, p, current_versions):
            continue
        outcome.assessed_count += 1
        accepted = {a["concept"]: a for a in p.attributes if a["concept"] and a["status"] == "supported"}
        conflicted = {a["concept"] for a in p.attributes if a["status"] == "contradicted"}
        # Alias additions cannot silently turn previous metadata acceptance into
        # unambiguous identity. Recheck against the current vocabulary.
        for pk in list(accepted):
            if accepted[pk]["attribute"] == "subject":
                labels = TaxonomyAlias.objects.filter(concept_id=pk).values("normalized")
                if (
                    TaxonomyAlias.objects.filter(normalized__in=labels, concept__release__version=versions()["release"])
                    .exclude(concept_id=pk)
                    .exists()
                ):
                    accepted.pop(pk)
        covered = requested & accepted.keys()
        complete = bool(requested) and covered == requested
        corroborated = complete and all(accepted[pk]["evidence_level"] == "corroborated" for pk in requested)
        portrait = any(a["attribute"] == "portrait" and a["status"] == "supported" for a in p.attributes)
        portrait_figures = max(
            (e.get("figures", 0) for a in p.attributes if a["attribute"] == "portrait" for e in a["evidence"]),
            default=0,
        )
        portrait = portrait and portrait_figures >= len(requested)
        event = accepted.get(parsed["event"])
        tier, relation, eligible = None, None, False
        if parsed["event"] and event and (complete or not requested):
            tier, relation = 1, "exact_event"
            eligible = corroborated and event["evidence_level"] == "corroborated"
        elif complete and portrait:
            tier, relation, eligible = 2, "subject_portrait", corroborated
        elif covered or event:
            tier, relation = 3, "related_specific"
        elif (
            not requested
            and not parsed["identity_constraint"]
            and not parsed["event_intent"]
            and themes & accepted.keys()
        ):
            tier, relation = 4, "thematic"
        elif requested or parsed["event_intent"]:
            # Only explicit sourced relations, never shared geography or identity.
            related = TaxonomyRelation.objects.filter(
                source_concept_id__in=requested | ({parsed["event"]} if parsed["event"] else set()),
                kind__in=["related_event", "theme"],
                target_id__in=accepted,
            )
            if related.exists():
                tier, relation = 4, "related_specific"
        if tier is None:
            continue
        if requested & conflicted or (parsed["event"] and parsed["event"] in conflicted):
            eligible = False
        if any(a["status"] == "contradicted" and a["attribute"] in {"subject", "event", "group"} for a in p.attributes):
            eligible = False
        eligible = bool(
            eligible
            and not parsed["unresolved"]
            and not parsed["adapter"]
            and themes <= accepted.keys()
            and request.auto_assign_policy in {"feast_strict", "content_suggest"}
        )
        evidence_level = (
            "corroborated"
            if corroborated
            else (
                "observed"
                if relation == "thematic"
                or (covered and all(accepted[pk]["evidence_level"] == "observed" for pk in covered))
                else "metadata"
            )
        )
        matches.append(
            {
                "id": icon.pk,
                "relation": relation,
                "match_tier": "direct_exact" if tier <= 2 else relation,
                "confidence": "high" if eligible else "low",
                "relevance": 100 if tier == 1 else 90 if tier == 2 else 60,
                "reason": relation,
                "rationale_code": relation,
                "auto_assignable": eligible,
                "matched_concepts": list(
                    TaxonomyConcept.objects.filter(pk__in=covered or (themes & accepted.keys()))
                    .order_by("pk")
                    .values_list("label", flat=True)
                ),
                "evidence_refs": [f"analysis:{p.analysis_id}"],
                "evidence": [],
                "covered_subjects": [i for i, pk in enumerate(sorted(requested)) if pk in covered],
                "identity_qualified": complete,
                "generic_portrait": portrait,
                "event_agrees": bool(event or portrait),
                "conflict": bool(conflicted),
                "full_request_coverage": complete,
                "concept_ids": sorted(covered or (themes & accepted.keys())),
                "coverage": {"required": len(requested), "covered": len(covered)},
                "evidence_level": evidence_level,
                "provenance": "taxonomy_rules",
                "unmet_constraints": parsed["unresolved"]
                or ([] if eligible else ["corroboration_or_depiction_required"]),
                "analysis_id": p.analysis_id,
                "revision": p.revision,
                "versions": p.analysis.versions,
                "_sort": (
                    tier,
                    -len(covered),
                    -len(themes & accepted.keys()),
                    0 if corroborated else 1,
                    normalize(icon.title),
                    icon.pk,
                ),
            }
        )
    # A viable exact event prevents automatic portrait fallback regardless of limit.
    if any(m["relation"] == "exact_event" for m in matches):
        for m in matches:
            if m["relation"] == "subject_portrait" and parsed["event_intent"]:
                m["auto_assignable"] = False
    if outcome.assessed_count != outcome.catalogue_count or versions() != current_versions:
        for m in matches:
            if parsed["event_intent"] or versions() != current_versions:
                m["auto_assignable"] = False
    matches.sort(key=lambda m: m["_sort"])
    for m in matches:
        m.pop("_sort")
    if versions() != current_versions:
        matches = []
        outcome.diagnostics.append("vocabulary_changed")
    outcome.matches = matches[: max(request.max_results, 0)]
    outcome.catalogue_complete = outcome.assessed_count == outcome.catalogue_count
    if not outcome.catalogue_complete:
        outcome.diagnostics.append("unclassified_or_stale_catalogue")
    outcome.elapsed_seconds = time.monotonic() - started
    return outcome


def assignment_current(icon, match):
    """Call with the icon row locked alongside the target assignment row."""
    if match.get("provenance") != "taxonomy_rules":
        return True
    p = (
        IconTaxonomyProjection.objects.filter(
            icon=icon, analysis_id=match.get("analysis_id"), revision=match.get("revision")
        )
        .select_related("analysis")
        .first()
    )
    return bool(p and projection_current(icon, p))
