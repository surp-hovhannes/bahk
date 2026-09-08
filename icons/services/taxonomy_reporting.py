"""Shared read-only operator summaries; private source prose never enters output."""

from collections import Counter

from django.db.models import Count, Q, Sum, BigIntegerField
from django.db.models.functions import Cast, Coalesce

from icons.models import Icon, IconTaxonomyProjection, IconTaxonomyWork, TaxonomyBudget, TaxonomyCall
from icons.services.taxonomy_evidence import normalize_comparison
from icons.services.taxonomy_freshness import projection_diagnostics

ERROR_CODES = {
    "attempts_exhausted",
    "stale_input",
    "observation_leased",
    "budget_exhausted_or_unconfigured",
    "pricing_not_configured",
    "model_price_underreserved",
    "provider_transport_failure",
    "analysis_processing_failure",
    "schema",
    "malformed_comparison",
    "oversized_evidence",
    "invalid_observation",
    "unknown_claim_concept",
    "missing_image",
    "image_too_large",
    "claims_too_large",
    "metadata_sources_too_large",
    "duplicate_json_key",
    "incomplete_response",
    "provider_disabled",
    "unsupported_taxonomy_model",
    "legacy_release_requires_upgrade",
}


def inspection(icon):
    if icon is None:
        return dict(
            state="deleted",
            analysis_id=None,
            assertion_counts={},
            freshness=["missing_projection"],
            diagnostic_codes=["icon_deleted"],
        )
    projection = IconTaxonomyProjection.objects.filter(icon=icon).select_related("analysis__observation").first()
    work = IconTaxonomyWork.objects.filter(icon=icon).first()
    latest = (
        projection.analysis
        if projection
        else icon.taxonomic_analyses.select_related("observation").order_by("-created_at").first()
    )
    attributes = projection.attributes if projection else []
    diagnostics = set()
    if latest and latest.observation_id and latest.comparison:
        try:
            _, _, entries = normalize_comparison(
                latest.claims.get("claims", []),
                latest.observation.evidence,
                {"assertions": latest.comparison.get("assertions")},
            )
            diagnostics.update(entry["code"] for entry in entries)
        except ValueError as exc:
            diagnostics.add(str(exc) if str(exc) in ERROR_CODES else "malformed_comparison")
    if work and work.error:
        diagnostics.add(work.error if work.error in ERROR_CODES else "legacy_or_unclassified_processing_error")
    return dict(
        state=work.state if work else "missing",
        analysis_id=latest.pk if latest else None,
        assertion_counts=dict(Counter(a["status"] + ":" + a["evidence_level"] for a in attributes)),
        freshness=projection_diagnostics(icon, projection),
        diagnostic_codes=sorted(diagnostics),
    )


def finalize_rows(rows, states=(), *, readonly=False):
    summary = Counter()
    for index, row in enumerate(rows):
        after = inspection(Icon.objects.prefetch_related("tags").filter(pk=row["id"]).first())
        result = states[index] if states else "read_only" if readonly else "enqueued"
        row.update(after_state=after.pop("state"), processing_result=result, **after)
        if result == "complete":
            category = "processed"
        elif result == "budget_blocked":
            category = "blocked"
        elif result in {"unavailable", "retry", "superseded", "deleted"}:
            category = "failed"
        elif row["action"] == "current" and row["after_state"] == "complete":
            category = "skipped_current"
        elif result == "not_due":
            category = "not_due"
        else:
            category = result
        summary[category] += 1
    return dict(summary)


def budget_report(name):
    if not name:
        return {"budget_reservations": None, "recorded_usage": None}
    reservations = (
        TaxonomyBudget.objects.filter(name=name)
        .values("enabled", "calls", "tokens", "microdollars", "max_calls", "max_tokens", "max_microdollars")
        .first()
    )
    usage = TaxonomyCall.objects.filter(budget__name=name).aggregate(
        returned_calls=Count("pk", filter=Q(state="returned")),
        unknown_calls=Count("pk", filter=~Q(state="returned")),
        **{
            key: Coalesce(Sum(Cast("usage__" + key, BigIntegerField())), 0) for key in ("input_tokens", "output_tokens")
        },
    )
    return {"budget_reservations": reservations, "recorded_usage": usage}
