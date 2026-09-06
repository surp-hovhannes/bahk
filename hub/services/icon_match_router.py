"""Explicit rollout boundary. Baseline prompts/provider behavior stay unchanged."""

import time

from django.conf import settings

from hub.services.icon_match_service import IconMatchOutcome, match_icons as baseline_match_icons


def router_mode(kind="content", church_id=None):
    scopes = getattr(settings, "ICON_MATCH_ROUTER_SCOPES", {})
    return scopes.get(f"{kind}:{church_id}", getattr(settings, "ICON_MATCH_ROUTER_MODE", "baseline"))


def match_icons(icons, request, *, church_id=None, commemoration=None, **kwargs):
    mode = router_mode(request.kind, church_id)
    if mode == "baseline":
        return baseline_match_icons(icons, request, **kwargs)
    from hub.services.icon_taxonomy_matching import match_icons as taxonomy_match_icons

    limits = kwargs.get("limits")
    deadline = time.monotonic() + limits.total_seconds if limits else None
    if mode == "shadow":
        icons = list(icons)
        outcome = baseline_match_icons(icons, request, **kwargs)
        if limits:
            # Public endpoint keeps its baseline latency budget. Run full shadow
            # comparison through offline eval or background consumers instead.
            outcome.diagnostics.append("taxonomy_shadow_skipped_endpoint_deadline")
        else:
            shadow = taxonomy_match_icons(
                icons, request, church_id=church_id, allow_adapter=False, commemoration=commemoration
            )
            outcome.diagnostics.append(f"taxonomy_shadow:{len(shadow.matches)}:{shadow.assessed_count}")
        return outcome
    if mode not in {"taxonomy", "taxonomy_suggestions"}:
        return IconMatchOutcome(diagnostics=["invalid_router_mode"])
    outcome = taxonomy_match_icons(icons, request, church_id=church_id, deadline=deadline, commemoration=commemoration)
    if mode == "taxonomy_suggestions":
        for match in outcome.matches:
            match["auto_assignable"] = False
    return outcome
