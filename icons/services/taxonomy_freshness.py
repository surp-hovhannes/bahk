"""Shared, read-only eligibility diagnostics. Never reads private image storage."""

from django.core.exceptions import ObjectDoesNotExist

from icons.services.taxonomy_inputs import dependencies_current, fingerprint, versions


def projection_diagnostics(icon, projection, current_versions=None):
    if projection is None:
        return ["missing_projection"]
    reasons = []
    analysis = projection.analysis
    expected = current_versions or versions()
    if projection.church_id != icon.church_id or analysis.church_id != icon.church_id:
        reasons.append("church_mismatch")
    if projection.fingerprint != fingerprint(icon):
        reasons.append("metadata_fingerprint_mismatch")
    if analysis.state != "complete":
        reasons.append("analysis_incomplete")
    if set(analysis.versions) - set(expected):
        reasons.append("version_unknown_component")
    for key in sorted(expected):
        if expected.get(key) != analysis.versions.get(key):
            reasons.append("version_" + key + "_mismatch")
    if not dependencies_current(analysis.dependencies):
        reasons.append("dependency_changed")
    if analysis.image_digest != icon.image_content_digest:
        reasons.append("image_digest_mismatch")
    saved = analysis.claims.get("metadata", {})
    if saved.get("image_revision") != icon.image_revision:
        reasons.append("image_revision_mismatch")
    try:
        work = icon.taxonomy_work
    except ObjectDoesNotExist:
        reasons.append("missing_work")
    else:
        if work.revision != projection.revision:
            reasons.append("work_revision_mismatch")
        if work.state != "complete":
            reasons.append("work_state_mismatch")
        if work.fingerprint != projection.fingerprint:
            reasons.append("work_fingerprint_mismatch")
    return reasons
