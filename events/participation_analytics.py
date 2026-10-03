"""Dated membership transitions, with audit history authoritative per user/fast.

Legacy Events remain a fallback only where a surviving profile/fast pair has no
periods. Missing timestamps never become dated transitions. Counts describe
operations, not unique participants or completions; current membership stays M2M.
"""

from collections import defaultdict

from django.db.models import BigIntegerField, Count, Exists, F, OuterRef, Q
from django.db.models.functions import Coalesce, TruncDate

from hub.models import FastParticipation, Profile
from .models import Event, EventType


def analytics_events():
    # Use a PK subquery so missing JSON keys cannot exclude legacy events via
    # SQL NULL semantics on SQLite/PostgreSQL.
    noops = Event.objects.filter(
        event_type__code__in=[EventType.USER_JOINED_FAST, EventType.USER_LEFT_FAST],
        data__participation_tracked=True, data__participation_changed=False,
    ).values("pk")
    return Event.objects.exclude(pk__in=noops)


def legacy_transition_events(code=None):
    audited_pair = FastParticipation.objects.filter(
        profile__user_id=OuterRef("user_id"),
    ).filter(Q(fast_id=OuterRef("object_id")) | Q(fast_original_id=OuterRef("object_id")))
    profile = Profile.objects.filter(user_id=OuterRef("user_id"))
    # Linked modern events cannot become legacy evidence after their audit row
    # was removed (for example by deleting and later recreating a profile).
    linked = Event.objects.filter(data__has_key="participation_id").exclude(
        data__participation_id=None,
    ).values("pk")
    qs = analytics_events().exclude(pk__in=linked).filter(
        content_type__app_label="hub", content_type__model="fast",
        event_type__code__in=[EventType.USER_JOINED_FAST, EventType.USER_LEFT_FAST],
    ).annotate(_audited_pair=Exists(audited_pair), _has_profile=Exists(profile)).filter(
        _audited_pair=False, _has_profile=True,
    )
    return qs.filter(event_type__code=code) if code else qs


def transition_counts(*, start=None, end=None, inclusive_end=False, user_id=None,
                      fast_id=None, group_by=None, tz=None, filters=None):
    """Return {bucket: {joins, leaves}} with four bounded aggregation queries.

    group_by is None, day, or fast. Window/filter rules are shared by periods and
    legacy fallback so audit migration cannot change an analytics filter's scope.
    """
    categories = dict(EventType.objects.filter(
        code__in=[EventType.USER_JOINED_FAST, EventType.USER_LEFT_FAST],
    ).values_list("code", "category")) if filters and (filters.get("include_categories") or filters.get("exclude_categories")) else {}
    result = defaultdict(lambda: {"joins": 0, "leaves": 0})
    for kind, field, code in (
        ("joins", "joined_at", EventType.USER_JOINED_FAST),
        ("leaves", "left_at", EventType.USER_LEFT_FAST),
    ):
        filters = filters or {}
        category = categories.get(code, EventType._get_category_for_code(code))
        if (filters.get("include_categories") and category not in filters["include_categories"]
                or category in filters.get("exclude_categories", [])
                or filters.get("only_event_types") and code not in filters["only_event_types"]
                or code in filters.get("exclude_event_types", [])):
            continue
        periods = FastParticipation.objects.filter(**{field + "__isnull": False})
        events = legacy_transition_events(code)
        if start is not None:
            periods = periods.filter(**{field + "__gte": start})
            events = events.filter(timestamp__gte=start)
        if end is not None:
            bound = "__lte" if inclusive_end else "__lt"
            periods = periods.filter(**{field + bound: end})
            events = events.filter(**{"timestamp" + bound: end})
        if user_id is not None:
            periods = periods.filter(profile__user_id=user_id)
            events = events.filter(user_id=user_id)
        if fast_id is not None:
            periods = periods.filter(Q(fast_id=fast_id) | Q(fast_original_id=fast_id))
            events = events.filter(object_id=fast_id)
        if filters.get("exclude_staff"):
            periods = periods.filter(profile__user__is_staff=False)
            events = events.filter(user__is_staff=False)
        if group_by == "day":
            periods = periods.annotate(_bucket=TruncDate(field, tzinfo=tz))
            events = events.annotate(_bucket=TruncDate("timestamp", tzinfo=tz))
        elif group_by == "fast":
            periods = periods.annotate(_bucket=Coalesce("fast_original_id", "fast_id", output_field=BigIntegerField()))
            events = events.annotate(_bucket=F("object_id"))
        elif group_by is not None:
            raise ValueError("Unsupported transition grouping")
        for qs in (periods, events):
            if group_by is None:
                result[None][kind] += qs.count()
            else:
                for row in qs.values("_bucket").annotate(count=Count("pk")).order_by():
                    result[row["_bucket"]][kind] += row["count"]
    return dict(result)
