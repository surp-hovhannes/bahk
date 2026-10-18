"""Read-only classification of screen events; historical rows stay untouched."""

from .models import EventType


def true_ui_screen_views(qs):
    """Screen visibility explicitly emitted by a v2 (or newer) UI client."""
    return qs.filter(
        event_type__code=EventType.SCREEN_VIEW,
        data__source="client_ui",
        data__schema_version__gte=2,
    )


def legacy_screen_views(qs):
    """All remaining screen views, including missing JSON keys.

    Use a PK subquery rather than negated JSON predicates: SQL NULL semantics
    otherwise lose rows with absent source/version keys.
    """
    return qs.filter(event_type__code=EventType.SCREEN_VIEW).exclude(
        pk__in=true_ui_screen_views(qs).values("pk"),
    )
