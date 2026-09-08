"""Independent fixed acceptance; model agreement/confidence cannot create proof."""

from icons.models import TaxonomyConcept, TaxonomyRelation
from icons.services.taxonomy_vocabulary import release_version, canonical_identity
from icons.services.taxonomy_evidence import (
    inscription_spans,
    normalize_comparison,
    observation_codes,
    signature_support,
)


def validate_assertions(claims, observation, comparison, *, sources=()):
    vocabulary = {
        c.pk: c for c in TaxonomyConcept.objects.filter(release__version=release_version()).prefetch_related("aliases")
    }
    claimed = {c["concept"] for c in claims}
    if not claimed <= vocabulary.keys():
        raise ValueError("unknown_claim_concept")
    observations = {o["id"]: o for o in observation["observations"]}
    contextual, duplicate_conflicts, diagnostics = normalize_comparison(claims, observation, comparison)
    aliases = {}
    for c in vocabulary.values():
        for a in c.aliases.all():
            aliases.setdefault(a.normalized, set()).add(c.pk)
    readable, unresolved_inscription = {}, False
    for obs in observations.values():
        if obs["kind"] != "inscription":
            continue
        spans = inscription_spans(obs)
        if not spans:
            unresolved_inscription = True
        for span in spans:
            ids = aliases.get(canonical_identity(span), set())
            if len(ids) == 1:
                pk = next(iter(ids))
                c = vocabulary[pk]
                if c.kind != "subject" or c.definition.get("qualified"):
                    readable.setdefault(pk, []).append(obs)
            else:
                unresolved_inscription = True
    signatures = signature_support(vocabulary, observations)
    metadata_subjects = {pk for pk in claimed if vocabulary[pk].kind == "subject"}
    observed_subjects = {pk for pk in readable if vocabulary[pk].kind == "subject"}
    title_subjects = {c["concept"] for c in claims if c["source"] == "title" and c["concept"] in metadata_subjects}
    qualified_tags = {
        c["concept"]
        for c in claims
        if c["source"] == "tag"
        and c["concept"] in metadata_subjects
        and vocabulary[c["concept"]].definition.get("qualified")
    }
    group_members = set()
    for claim in claims:
        group = vocabulary[claim["concept"]]
        if (
            group.kind == "group"
            and claim["source"] != "filename"
            and (claim["source"] == "title" or group.definition.get("qualified") or group.source.get("references"))
        ):
            group_members.update(group.definition.get("members", []))
    authoritative_subjects = title_subjects | qualified_tags | (group_members & metadata_subjects)
    weak_tag_subjects = {
        c["concept"] for c in claims if c["source"] == "tag" and c["concept"] in metadata_subjects
    } - authoritative_subjects
    weak_subjects = {c["concept"] for c in claims if c["source"] == "filename" and c["concept"] in metadata_subjects}
    weak_conflict = bool(authoritative_subjects and weak_subjects - authoritative_subjects)
    weak_conflict |= any(
        s.get("weak") and s["parsed"]["identity_constraint"] and s["parsed"]["unresolved"] for s in sources
    )
    by_source = {}
    for claim in claims:
        if claim["concept"] in authoritative_subjects and claim["source"] != "filename":
            by_source.setdefault((claim["source"], claim["text"]), set()).add(claim["concept"])
    sets = list(by_source.values())
    whole_groups = [
        set(vocabulary[pk].definition.get("members", []))
        for pk in {c["concept"] for c in claims if c["source"] != "filename"}
        if vocabulary[pk].kind == "group"
    ]
    whole_groups += [value for (source, _), value in by_source.items() if source == "title" and len(value) > 1]
    covered_by_group = any(set().union(*sets) <= group for group in whole_groups) if sets else False
    metadata_conflict = not covered_by_group and any(not (a <= b or b <= a) for a in sets for b in sets)
    competing = bool(
        observation["depiction"] == "portrait"
        and observation["figures"] == 1
        and not unresolved_inscription
        and authoritative_subjects
        and observed_subjects - authoritative_subjects
        and not any(observed_subjects <= group for group in whole_groups)
    )
    visible_codes = set().union(*(observation_codes(o) for o in observations.values()))
    observed_events = {
        pk
        for pk, c in vocabulary.items()
        if c.kind == "event"
        and c.definition.get("observations_all")
        and observation["depiction"] == "scene"
        and set(c.definition["observations_all"]) <= visible_codes
    }
    candidates = claimed | readable.keys() | signatures.keys() | observed_events
    result = []
    for pk in sorted(candidates):
        c = vocabulary[pk]
        refs = [claim for claim in claims if claim["concept"] == pk]
        refs += [{"source": "independent_observation", **o} for o in readable.get(pk, [])]
        conflict = bool(c.kind == "subject" and competing and pk in metadata_subjects and pk not in observed_subjects)
        refs.append(
            {
                "source": "comparison_diagnostics",
                "codes": sorted({d["code"] for d in diagnostics}),
                "visual_compatibility": "compatible" if contextual.get(pk, {}).get("agrees") else "unknown",
                "metadata_ambiguity": metadata_conflict,
                "weak_identity_conflict": weak_conflict,
                "weak_unqualified_tag": pk in weak_tag_subjects,
            }
        )
        status, level, rule = "unknown", "none", "insufficient_independent_evidence"
        ambiguous = any(len(aliases[a.normalized]) > 1 for a in c.aliases.all())
        if c.kind == "subject" and c.definition.get("qualified") and not ambiguous:
            if pk in authoritative_subjects:
                status, level, rule = "supported", "metadata", "qualified_source_metadata"
            if pk in signatures and pk in authoritative_subjects and not unresolved_inscription and not weak_conflict:
                status, level, rule = "supported", "corroborated", "sourced_unique_visual_signature"
                refs += [{"source": "independent_signature", **o} for o in signatures[pk]]
            if pk in readable:
                status, level, rule = "supported", "observed", "independent_qualified_inscription"
                if pk in authoritative_subjects and not unresolved_inscription and not weak_conflict:
                    level = "corroborated"
        elif (
            c.kind == "subject" and c.definition.get("source_marked") and pk in authoritative_subjects and not ambiguous
        ):
            status, level, rule = "supported", "metadata", "source_marked_unqualified_suggestion"
        elif c.kind == "theme":
            codes = set(c.definition.get("activities", [])) & visible_codes
            if codes:
                status, level, rule = "supported", "observed", "observable_activity"
                refs += [
                    {"source": "independent_observation", **o}
                    for o in observations.values()
                    if observation_codes(o) & codes
                ]
        elif c.kind == "event":
            members = set(c.definition.get("members", []))
            if pk in claimed:
                status, level, rule = "supported", "metadata", "explicit_event_metadata"
            if pk in observed_events or (pk in readable and observation["depiction"] == "scene"):
                status, level, rule = "supported", "observed", "sourced_observable_scene"
                refs += [
                    {"source": "independent_observation", **o}
                    for o in observations.values()
                    if observation_codes(o) & set(c.definition.get("observations_all", []))
                ]
            if (
                pk in readable
                and members
                and members <= readable.keys()
                and observation["depiction"] == "scene"
                and not unresolved_inscription
                and pk in claimed
            ):
                status, level, rule = "supported", "corroborated", "event_and_participant_inscriptions"
        elif c.kind == "group" and not ambiguous:
            members = set(c.definition.get("members", []))
            if pk in claimed:
                status, level, rule = "supported", "metadata", "explicit_group_metadata"
            complete_members = bool(
                c.definition.get("complete")
                and members
                and members <= readable.keys()
                and observation["figures"] >= len(members)
            )
            exact_group = pk in readable and bool(c.source)
            if complete_members or exact_group:
                status, level, rule = "supported", "observed", "complete_members_or_sourced_group_inscription"
                if pk in claimed and not unresolved_inscription:
                    level = "corroborated"
        if pk in duplicate_conflicts or (metadata_conflict and c.kind in {"subject", "group", "event"}):
            status, level, rule = "unknown", "none", "comparison_or_metadata_ambiguity"
        if conflict:
            refs += [
                {"source": "incompatible_independent_inscription", "concept": other, **o}
                for other in observed_subjects - authoritative_subjects
                for o in readable[other]
            ]
            status, level, rule = "contradicted", "none", "affirmative_single_portrait_incompatible_inscription"
        result.append(dict(concept=pk, attribute=c.kind, status=status, evidence_level=level, rule=rule, evidence=refs))
    # Nonidentity suggestions survive identity disagreement. Activity and explicitly
    # sourced scene/theme mappings work even when all source metadata is generic.
    accepted_events = {a["concept"]: a for a in result if a["attribute"] == "event" and a["status"] == "supported"}
    relations = list(
        TaxonomyRelation.objects.filter(
            source_concept_id__in=accepted_events, kind="theme", target__release__version=release_version()
        )
    )
    for pk, theme in vocabulary.items():
        if theme.kind != "theme":
            continue
        codes = set(theme.definition.get("activities", [])) & visible_codes
        mapped = [r for r in relations if r.target_id == pk and r.source]
        if not codes and not mapped:
            continue
        evidence = [
            {"source": "independent_observation", **o} for o in observations.values() if observation_codes(o) & codes
        ]
        evidence += [
            {
                "source": "sourced_event_relation",
                "relation": r.pk,
                "event": r.source_concept_id,
                "event_evidence_level": accepted_events[r.source_concept_id]["evidence_level"],
            }
            for r in mapped
        ]
        result = [a for a in result if a["concept"] != pk]
        level = "inferred"
        result.append(
            dict(
                concept=pk,
                attribute="theme",
                status="supported",
                evidence_level=level,
                rule="inferred_from_activity_or_sourced_scene",
                evidence=evidence,
            )
        )
    scene_claim = any(vocabulary[pk].kind == "event" for pk in claimed) or any(
        source["scene_hint"] for source in sources
    )
    portrait = observation["depiction"] == "portrait" and not scene_claim
    result.append(
        dict(
            concept=None,
            attribute="portrait",
            status="supported" if portrait else "unknown",
            evidence_level="observed" if portrait else "none",
            rule="scene_claim_blocks_portrait" if scene_claim else "affirmative_portrait_no_scene_claim",
            evidence=[{"figures": observation["figures"]}, *sources],
        )
    )
    return result
