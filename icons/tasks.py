"""Existing Celery integration; the explicit dispatcher also works without beat."""

from celery import shared_task
from django.conf import settings

from icons.services.taxonomy_pipeline import due_work, process_icon


@shared_task
def analyze_icon_taxonomy(icon_id, budget_name=None):
    return process_icon(icon_id, budget_name=budget_name)


@shared_task
def dispatch_icon_taxonomy(limit=20):
    if not getattr(settings, "ICON_TAXONOMY_DISPATCH_ENABLED", False):
        return {"status": "disabled", "dispatched": 0}
    from django.db.models import F
    from icons.models import TaxonomyBudget

    if not TaxonomyBudget.objects.filter(
        name=getattr(settings, "ICON_TAXONOMY_BUDGET", ""),
        enabled=True,
        calls__lt=F("max_calls"),
        tokens__lt=F("max_tokens"),
        microdollars__lt=F("max_microdollars"),
    ).exists():
        return {"status": "budget_blocked", "dispatched": 0}
    from icons.services.ingestion import reconcile_versions

    reconcile_versions(limit=min(max(limit, 0), 100))
    count = 0
    for icon_id in due_work().values_list("icon_id", flat=True)[: min(max(limit, 0), 100)]:
        # Broker delivery is deliberately not the source of truth. Duplicate deliveries
        # are harmless; a lost message is recovered by the next explicit dispatch.
        analyze_icon_taxonomy.delay(icon_id)
        count += 1
    return {"status": "complete", "dispatched": count}
