"""Backfill ``FastParticipation`` from existing USER_JOINED_FAST / USER_LEFT_FAST events.

The new ``m2m_changed`` receiver keeps ``FastParticipation`` in sync going
forward; this migration reconstructs the history from the event log so that
analytics can answer "did this user complete fast X?" for rows that pre-date
the receiver.

Rules:

* For every (profile, fast) pair seen in the event log, walk join/leave events
  in timestamp order: a join opens a row (joined_at=event.timestamp,
  left_at=NULL), a leave stamps left_at on the open row (or creates a row with
  joined_at=NULL, left_at=event.timestamp if no prior join).
* Anomalies (a second join without a leave in between, a leave without a
  join) are tolerated -- they fall back to "leave first, reopen" so the unique
  partial index holds.
* After walking events, current ``Profile.fasts`` membership that has no open
  period gets one with ``joined_at`` taken from the latest matching join event
  (NULL if there is no event -- "unknown" is preserved, not guessed).
* ``Event.target`` is a generic relation (content_type + object_id); events
  whose target is not a Fast are ignored.

Irreversible in substance: deleting the rows this migration creates cannot
recreate events that may no longer exist.
"""

from collections import defaultdict

from django.db import migrations

JOIN_CODE = "user_joined_fast"
LEAVE_CODE = "user_left_fast"


def backfill_fast_participation(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    EventType = apps.get_model("events", "EventType")
    Fast = apps.get_model("hub", "Fast")
    FastParticipation = apps.get_model("hub", "FastParticipation")
    Profile = apps.get_model("hub", "Profile")
    ContentType = apps.get_model("contenttypes", "ContentType")

    event_types = {
        et.code: et
        for et in EventType.objects.filter(
            code__in=[JOIN_CODE, LEAVE_CODE],
        )
    }
    if JOIN_CODE not in event_types:
        # No matching events to seed -- nothing to backfill.
        return
    join_type_id = event_types[JOIN_CODE].id
    leave_type_id = event_types[LEAVE_CODE].id if LEAVE_CODE in event_types else None

    fast_content_type = ContentType.objects.get_for_model(Fast)
    user_to_profile = {p.user_id: p.id for p in Profile.objects.only("id", "user_id")}

    # (profile_id, fast_id) -> list of (timestamp, action_code)
    history = defaultdict(list)
    relevant_events = (
        Event.objects.filter(
            content_type=fast_content_type,
            event_type_id__in=[join_type_id] + ([leave_type_id] if leave_type_id else []),
        )
        .order_by("timestamp")
        .only("user_id", "object_id", "timestamp", "event_type_id")
    )

    for ev in relevant_events:
        profile_id = user_to_profile.get(ev.user_id)
        if profile_id is None:
            continue
        action = "join" if ev.event_type_id == join_type_id else "leave"
        history[(profile_id, ev.object_id)].append((ev.timestamp, action))

    # Pass 1: replay the event log per (profile, fast).
    for (profile_id, fast_id), events in history.items():
        open_period = None
        for ts, action in events:
            if action == "join":
                if open_period is not None:
                    # Two joins with no leave in between: treat the second as
                    # the user's intent, closing the prior period at the same
                    # timestamp so the partial-unique index holds.
                    open_period.left_at = ts
                    open_period.save(update_fields=["left_at"])
                open_period = FastParticipation.objects.create(
                    profile_id=profile_id,
                    fast_id=fast_id,
                    joined_at=ts,
                    left_at=None,
                )
            else:  # leave
                if open_period is not None:
                    open_period.left_at = ts
                    open_period.save(update_fields=["left_at"])
                    open_period = None
                else:
                    # Leave without a prior join: the leave is ground truth.
                    FastParticipation.objects.create(
                        profile_id=profile_id,
                        fast_id=fast_id,
                        joined_at=None,
                        left_at=ts,
                    )

    # Pass 2: mark current membership with an open period if none exists.
    latest_join_by_event = {}  # (user_id, fast_id) -> latest join timestamp
    for user_id, object_id, ts in (
        Event.objects.filter(
            event_type_id=join_type_id,
            content_type=fast_content_type,
        )
        .order_by("timestamp")
        .values_list("user_id", "object_id", "timestamp")
    ):
        latest_join_by_event[(user_id, object_id)] = ts

    for profile in Profile.objects.iterator(chunk_size=500):
        current_fast_ids = list(
            profile.fasts.values_list("id", flat=True),
        )
        for fast_id in current_fast_ids:
            already_open = FastParticipation.objects.filter(
                profile_id=profile.id,
                fast_id=fast_id,
                left_at__isnull=True,
            ).exists()
            if already_open:
                continue
            joined_at = latest_join_by_event.get((profile.user_id, fast_id))
            FastParticipation.objects.create(
                profile_id=profile.id,
                fast_id=fast_id,
                joined_at=joined_at,
                left_at=None,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("hub", "0070_fastparticipation"),
        ("events", "0014_drop_duplicate_analytics_indexes"),
    ]

    operations = [
        migrations.RunPython(
            backfill_fast_participation,
            migrations.RunPython.noop,
        ),
    ]
