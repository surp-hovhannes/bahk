from django.db.models import Q
from moderation.models import Responsibility


def allowed(user, capability):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and Responsibility.objects.filter(user=user, **{capability: True}).exists()
    )


def crisis_query():
    return Q(moderation_severity="critical") | Q(moderation_result__llm_check__suggested_action="escalate")


def is_crisis(prayer):
    result = prayer.moderation_result if isinstance(prayer.moderation_result, dict) else {}
    evidence = result.get("llm_check")
    return prayer.moderation_severity == "critical" or (
        isinstance(evidence, dict) and evidence.get("suggested_action") == "escalate"
    )


def visible_prayers(queryset, user):
    if allowed(user, "crisis"):
        return queryset if allowed(user, "general") else queryset.filter(crisis_query())
    return queryset.exclude(crisis_query()) if allowed(user, "general") else queryset.none()
