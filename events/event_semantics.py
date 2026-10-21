"""Read-only classification of screen events; historical rows stay untouched."""

from django.db.models import Q

from .models import EventType


TRUE_UI_DATA_Q = Q(data__source="client_ui", data__schema_version__gte=2)
TRUE_UI_SCREEN_VIEW_Q = Q(event_type__code=EventType.SCREEN_VIEW) & TRUE_UI_DATA_Q


def true_ui_screen_views(qs):
    """Screen visibility explicitly emitted by a v2 (or newer) UI client."""
    return qs.filter(TRUE_UI_SCREEN_VIEW_Q)


def legacy_screen_views(qs):
    """All remaining screen views, including missing JSON keys.

    Use a PK subquery rather than negated JSON predicates: SQL NULL semantics
    otherwise lose rows with absent source/version keys.
    """
    return qs.filter(event_type__code=EventType.SCREEN_VIEW).exclude(
        pk__in=true_ui_screen_views(qs).values("pk"),
    )


def exclude_legacy_screen_views(qs):
    """Drop legacy screen views and keep every other event type.

    Matches the screen_view type by id, so the outer query needs no join to
    events_eventtype. The event_type FK is non-null: a NULL JSON branch fails
    the filter only for screen_view rows; the non-screen branch keeps the rest.
    """
    screen_type = EventType.objects.filter(code=EventType.SCREEN_VIEW).values("pk")
    return qs.filter(~Q(event_type__in=screen_type) | TRUE_UI_DATA_Q)


def user_visible_events(qs):
    """Exclude private lifecycle telemetry without joining the event type table."""
    return qs.exclude(event_type_id__in=EventType.objects.filter(
        code__in=sorted(EventType.NOTIFICATION_LIFECYCLE_CODES),
    ).values('pk'))
