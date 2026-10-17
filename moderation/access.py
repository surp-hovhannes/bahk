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
    # One indexed column. The moderation task writes "critical" for every
    # escalation and migration 0002 backfilled older rows. Never test a JSON key
    # here: a row without the key compares as NULL, and ``.exclude()`` would
    # then hide it as well (failed moderations vanished from every queue).
    return Q(**{f"{prefix}moderation_severity": "critical"})


def hide_crisis(queryset, request, prefix=""):
    """Drop crisis prayers (or rows referencing one via ``prefix``) unless the user is a crisis responder."""
    return queryset if can(request, "crisis") else queryset.exclude(crisis_query(prefix))


def is_crisis(prayer):
    """Python twin of ``crisis_query``; the two must always agree."""
    return prayer.moderation_severity == "critical"


def visible_prayers(queryset, request):
    if can(request, "crisis"):
        return queryset if can(request, "general") else queryset.filter(crisis_query())
    return queryset.exclude(crisis_query()) if can(request, "general") else queryset.none()
