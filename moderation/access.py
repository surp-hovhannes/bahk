from django.db.models import Q
from moderation.models import Responsibility


def allowed(user, capability):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and Responsibility.objects.filter(user=user, **{capability: True}).exists()
    )


def can(request, capability):
    """``allowed`` for ``request.user``, looked up once per request."""
    cache = request.__dict__.setdefault("_moderation_capabilities", {})
    if capability not in cache:
        cache[capability] = allowed(getattr(request, "user", None), capability)
    return cache[capability]


def crisis_query(prefix=""):
    return Q(**{f"{prefix}moderation_severity": "critical"}) | Q(
        **{f"{prefix}moderation_result__llm_check__suggested_action": "escalate"}
    )


def hide_crisis(queryset, request, prefix=""):
    """Drop crisis prayers (or rows referencing one via ``prefix``) unless the user is a crisis responder."""
    return queryset if can(request, "crisis") else queryset.exclude(crisis_query(prefix))


def is_crisis(prayer):
    result = prayer.moderation_result if isinstance(prayer.moderation_result, dict) else {}
    evidence = result.get("llm_check")
    return prayer.moderation_severity == "critical" or (
        isinstance(evidence, dict) and evidence.get("suggested_action") == "escalate"
    )


def visible_prayers(queryset, request):
    if can(request, "crisis"):
        return queryset if can(request, "general") else queryset.filter(crisis_query())
    return queryset.exclude(crisis_query()) if can(request, "general") else queryset.none()
