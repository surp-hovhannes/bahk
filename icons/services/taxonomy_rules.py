"""Independent fixed acceptance; model agreement/confidence cannot create proof."""

from icons.models import TaxonomyConcept, TaxonomyRelation
from icons.services.taxonomy_inputs import normalize
from icons.services.taxonomy_vocabulary import release_version


def validate_assertions(claims, observation, comparison):
    vocabulary = {
        c.pk: c for c in TaxonomyConcept.objects.filter(release__version=release_version()).prefetch_related("aliases")
    }
    claimed = {c["concept"] for c in claims}
    if not claimed <= vocabulary.keys():
        raise ValueError("unknown_claim_concept")
    observations = {o["id"]: o for o in observation["observations"]}
    contextual = {}
    for item in comparison["assertions"]:
        if (
            item["concept"] not in claimed
            or not set(item["observation_ids"]) <= observations.keys()
            or item["concept"] in contextual
        ):
            raise ValueError("invalid_comparison_reference")
        contextual[item["concept"]] = item
    aliases = {}
    for c in vocabulary.values():
        for a in c.aliases.all():
            aliases.setdefault(a.normalized, set()).add(c.pk)
    readable, unresolved_inscription = {}, False
    for obs in observations.values():
        if obs["kind"] == "inscription" and obs["readable"]:
            ids = aliases.get(normalize(obs["text"]), set())
            if len(ids) == 1:
                pk = next(iter(ids))
                c = vocabulary[pk]
                if c.kind != "subject" or c.definition.get("qualified"):
                    readable.setdefault(pk, []).append(obs)
            else:
                unresolved_inscription = True
    metadata_subjects = {pk for pk in claimed if vocabulary[pk].kind == "subject"}
    observed_subjects = {pk for pk in readable if vocabulary[pk].kind == "subject"}
    by_source = {}
    for claim in claims:
        if claim["concept"] in metadata_subjects:
            by_source.setdefault((claim["source"], claim["text"]), set()).add(claim["concept"])
    sets = list(by_source.values())
    whole_groups = [
        set(vocabulary[pk].definition.get("members", [])) for pk in claimed if vocabulary[pk].kind == "group"
    ]
    whole_groups += [value for (source, _), value in by_source.items() if source == "title" and len(value) > 1]
    covered_by_group = any(set().union(*sets) <= group for group in whole_groups) if sets else False
    metadata_conflict = not covered_by_group and any(not (a <= b or b <= a) for a in sets for b in sets)
    competing = bool(
        metadata_subjects
        and observed_subjects - metadata_subjects
        and not any(observed_subjects <= group for group in whole_groups)
    )
    visible_codes = {o["text"] for o in observations.values() if o["kind"] in {"activity", "object", "depiction"}}
    observed_events = {
        pk
        for pk, c in vocabulary.items()
        if c.kind == "event"
        and c.definition.get("observations_all")
        and observation["depiction"] == "scene"
        and set(c.definition["observations_all"]) <= visible_codes
    }
    candidates = claimed | readable.keys() | observed_events
    result = []
    for pk in sorted(candidates):
        c = vocabulary[pk]
        refs = [claim for claim in claims if claim["concept"] == pk]
        refs += [{"source": "independent_observation", **o} for o in readable.get(pk, [])]
        conflict = contextual.get(pk, {}).get("conflict", False) or (
            c.kind in {"subject", "event", "group"} and (competing or metadata_conflict)
        )
        status, level, rule = "unknown", "none", "insufficient_independent_evidence"
        ambiguous = any(len(aliases[a.normalized]) > 1 for a in c.aliases.all())
        if c.kind == "subject" and c.definition.get("qualified") and not ambiguous:
            if pk in claimed:
                status, level, rule = "supported", "metadata", "qualified_source_metadata"
            if pk in readable:
                status, level, rule = "supported", "observed", "independent_qualified_inscription"
                if pk in claimed and not unresolved_inscription:
                    level = "corroborated"
        elif c.kind == "theme":
            codes = set(c.definition.get("activities", [])) & visible_codes
            if codes:
                status, level, rule = "supported", "observed", "observable_activity"
                refs += [
                    {"source": "independent_observation", **o} for o in observations.values() if o["text"] in codes
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
                    if o["text"] in c.definition.get("observations_all", [])
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
        if conflict:
            status, level, rule = "contradicted", "none", "competing_claim_or_observation"
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
        evidence = [{"source": "independent_observation", **o} for o in observations.values() if o["text"] in codes]
        evidence += [
            {"source": "sourced_event_relation", "relation": r.pk, "event": r.source_concept_id} for r in mapped
        ]
        result = [a for a in result if a["concept"] != pk]
        level = (
            "observed"
            if codes or any(accepted_events[r.source_concept_id]["evidence_level"] != "metadata" for r in mapped)
            else "metadata"
        )
        result.append(
            dict(
                concept=pk,
                attribute="theme",
                status="supported",
                evidence_level=level,
                rule="observable_activity_or_sourced_event_theme",
                evidence=evidence,
            )
        )
    scene_claim = any(vocabulary[pk].kind == "event" for pk in claimed)
    portrait = observation["depiction"] == "portrait" and not scene_claim
    result.append(
        dict(
            concept=None,
            attribute="portrait",
            status="supported" if portrait else "unknown",
            evidence_level="observed" if portrait else "none",
            rule="affirmative_portrait_no_scene_claim",
            evidence=[{"figures": observation["figures"]}],
        )
    )
    return result
