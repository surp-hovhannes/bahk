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

The reverse preserves audit rows. Forward replay reconciles complete periods
with existing rows, so reverse/reapply never duplicates history or overwrites
periods recorded by the live receiver.
"""

from collections import Counter, defaultdict

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

    db_alias = schema_editor.connection.alias
    Event = Event.objects.using(db_alias)
    EventType = EventType.objects.using(db_alias)
    Fast = Fast.objects.using(db_alias)
    FastParticipation = FastParticipation.objects.using(db_alias)
    Profile = Profile.objects.using(db_alias)
    ContentType = ContentType.objects.using(db_alias)

    event_types = {
        et.code: et
        for et in EventType.filter(
            code__in=[JOIN_CODE, LEAVE_CODE],
        )
    }
    join_type_id = event_types[JOIN_CODE].id if JOIN_CODE in event_types else None

    # Use ordinary fields: historical ContentType models need no runtime methods.
    fast_content_type = ContentType.filter(app_label="hub", model="fast").first()
    if fast_content_type is None:
        # Content types may not exist until post_migrate on a fresh database.
        Event = Event.none()
    valid_fast_ids = set(Fast.values_list("id", flat=True))
    user_to_profile = {p.user_id: p.id for p in Profile.only("id", "user_id")}

    # (profile_id, fast_id) -> list of (timestamp, action_code)
    history = defaultdict(list)
    relevant_events = (
        Event.filter(
            content_type=fast_content_type,
            event_type_id__in=[et.id for et in event_types.values()],
        )
        .order_by("timestamp", "id")
        .only("user_id", "object_id", "timestamp", "event_type_id")
    )

    for ev in relevant_events:
        profile_id = user_to_profile.get(ev.user_id)
        if profile_id is None or ev.object_id not in valid_fast_ids:
            continue
        action = "join" if ev.event_type_id == join_type_id else "leave"
        history[(profile_id, ev.object_id)].append((ev.timestamp, action))

    # Derive complete periods before inserting: replay must not temporarily
    # open a historical period while an existing final open period remains.
    for (profile_id, fast_id), events in history.items():
        periods = []
        open_period = None
        for ts, action in events:
            if action == "join":
                if open_period is not None:
                    open_period[1] = ts
                open_period = [ts, None]
                periods.append(open_period)
            elif open_period is not None:
                open_period[1] = ts
                open_period = None
            else:
                periods.append([None, ts])

        existing = FastParticipation.filter(profile_id=profile_id, fast_id=fast_id)
        recorded = Counter(existing.values_list("joined_at", "left_at"))
        expected = Counter(tuple(period) for period in periods)
        for (joined_at, left_at), count in expected.items():
            # Keep any live open period, even if its timestamp differs from
            # the historical join. Never close or overwrite runtime history.
            if left_at is None and existing.filter(left_at__isnull=True).exists():
                continue
            for _ in range(max(0, count - recorded[(joined_at, left_at)])):
                FastParticipation.create(
                    profile_id=profile_id,
                    fast_id=fast_id,
                    joined_at=joined_at,
                    left_at=left_at,
                )

    # Pass 2: mark current membership with an open period if none exists.
    latest_join_by_event = {}  # (user_id, fast_id) -> latest join timestamp
    for user_id, object_id, ts in (
        Event.filter(
            event_type_id=join_type_id,
            content_type=fast_content_type,
        )
        .order_by("timestamp", "id")
        .values_list("user_id", "object_id", "timestamp")
    ):
        latest_join_by_event[(user_id, object_id)] = ts

    for profile in Profile.iterator(chunk_size=500):
        current_fast_ids = list(
            profile.fasts.using(db_alias).values_list("id", flat=True),
        )
        for fast_id in current_fast_ids:
            already_open = FastParticipation.filter(
                profile_id=profile.id,
                fast_id=fast_id,
                left_at__isnull=True,
            ).exists()
            if already_open:
                continue
            joined_at = latest_join_by_event.get((profile.user_id, fast_id))
            FastParticipation.create(
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
