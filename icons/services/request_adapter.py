"""One bounded, cached theme interpretation. It can never assign an icon."""

import time

from django.conf import settings

from icons.models import TaxonomyConcept, TaxonomyRequestInterpretation
from icons.services.taxonomy_inputs import ADAPTER, digest, versions
from icons.services.taxonomy_pipeline import wire_call
from icons.services.vision_provider import ADAPTER_SCHEMA, VisionProvider


def adapt_request(request, *, church_id=None, provider=None, deadline=None):
    if provider is None and not getattr(settings, "ICON_TAXONOMY_REQUEST_ADAPTER_ENABLED", False):
        return {"concepts": [], "unresolved": [request.primary_text]}
    concepts = list(
        TaxonomyConcept.objects.filter(kind="theme", release__version=versions()["release"])
        .order_by("pk")
        .values("id", "label", "definition")
    )[:64]
    payload = {"text": request.primary_text, "context": list(request.context_terms), "concepts": concepts}
    key = digest({"payload": payload, "scope": church_id, "adapter": ADAPTER, "versions": versions()})
    cached = TaxonomyRequestInterpretation.objects.filter(key=key).first()
    if cached:
        return cached.result
    result = {"concepts": [], "unresolved": [request.primary_text]}
    try:
        remaining = deadline - time.monotonic() if deadline is not None else 20
        if remaining <= 0.1:
            return result
        value, _, _ = wire_call(
            provider or VisionProvider(), "adapt", payload, ADAPTER_SCHEMA, timeout=min(remaining, 20)
        )
        allowed = {c["id"] for c in concepts}
        if not set(value["concepts"]) <= allowed:
            raise ValueError("unknown_adapter_concept")
        source = request.primary_text + " " + " ".join(request.context_terms)
        if any(span not in source for span in value["unresolved"]):
            raise ValueError("invented_unresolved_span")
        result = value
        TaxonomyRequestInterpretation.objects.get_or_create(
            key=key, defaults={"result": result, "church_id": church_id}
        )
    except Exception:
        # Controlled lexical interpretation remains available to the caller.
        pass
    return result
