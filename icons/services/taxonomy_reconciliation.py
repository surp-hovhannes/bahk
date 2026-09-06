"""Refresh selected dependency-stale projections using immutable retained evidence."""

from django.db import transaction
from django.utils import timezone

from icons.models import Icon, IconAnalysis, IconAssertion, IconTaxonomyProjection, IconTaxonomyWork
from icons.services.taxonomy_freshness import projection_diagnostics
from icons.services.taxonomy_inputs import analysis_dependencies, dependencies_current, digest, metadata
from icons.services.taxonomy_rules import validate_assertions
from icons.services.taxonomy_vocabulary import catalogue_claims, catalogue_sources


def reconcile_selected_projections(icon_ids):
    reconciled = []
    for icon_id in icon_ids:
        # Match ingestion lock order; never reclaim another worker's work or
        # promote a failed/incomplete analysis during this deterministic pass.
        with transaction.atomic():
            icon = Icon.objects.select_for_update().filter(pk=icon_id).first()
            if icon is None:
                continue
            work = IconTaxonomyWork.objects.select_for_update().filter(icon=icon).first()
            projection = (
                IconTaxonomyProjection.objects.select_for_update(of=("self",))
                .filter(icon=icon)
                .select_related("analysis__observation")
                .first()
            )
            if not work or work.state != "complete" or not projection:
                continue
            if projection_diagnostics(icon, projection) != ["dependency_changed"]:
                continue
            prior = projection.analysis
            if not prior.observation_id or prior.observation.state != "complete":
                continue
            inputs = metadata(icon)
            # All cohort vocabulary growth is finished. Read-only resolution
            # here cannot stale a projection reconciled earlier in this pass.
            claims = catalogue_claims(inputs, create=False)
            sources = catalogue_sources(inputs)
            observation = prior.observation.evidence
            dependencies = analysis_dependencies(inputs, claims, observation)
            retained_assertions = prior.comparison.get("assertions") if isinstance(prior.comparison, dict) else None
            retained_comparison = {
                "assertions": retained_assertions if isinstance(retained_assertions, (dict, list)) else []
            }
            assertions = validate_assertions(claims, observation, retained_comparison, sources=sources)
            if not dependencies_current(dependencies):
                continue
            key = digest(
                {
                    "metadata": inputs,
                    "image": prior.image_digest,
                    "versions": prior.versions,
                    "dependencies": dependencies["digest"],
                }
            )
            analysis, created = IconAnalysis.objects.get_or_create(
                icon=icon,
                digest=key,
                defaults={
                    "church_id": icon.church_id,
                    "image_digest": prior.image_digest,
                    "versions": prior.versions,
                    "dependencies": dependencies,
                    "claims": {
                        "metadata": inputs,
                        "claims": claims,
                        "sources": sources,
                        "retained_analysis_id": prior.pk,
                    },
                    "observation": prior.observation,
                    "comparison": prior.comparison,
                    "state": "complete",
                    "completed_at": timezone.now(),
                },
            )
            if not created and analysis.state != "complete":
                continue
            if created:
                IconAssertion.objects.bulk_create(
                    [
                        IconAssertion(
                            analysis=analysis, concept_id=a["concept"], **{k: v for k, v in a.items() if k != "concept"}
                        )
                        for a in assertions
                    ]
                )
            else:
                assertions = [
                    {"concept": a.pop("concept_id"), **a}
                    for a in analysis.assertions.values(
                        "concept_id", "attribute", "status", "evidence_level", "rule", "evidence"
                    )
                ]
            projection.analysis = analysis
            projection.attributes = assertions
            projection.save(update_fields=["analysis", "attributes"])
            reconciled.append(icon_id)
    if reconciled:
        from icons.cache import IconViewCache

        IconViewCache.clear_all()
    return reconciled
