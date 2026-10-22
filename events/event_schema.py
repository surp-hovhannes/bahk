"""Read-time analytics semantics; these classifications never rewrite stored rows."""

import re
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.utils import timezone

from .models import EventType

EVENT_SCHEMA_VERSION = 2

NOTIFICATION_LIFECYCLE_EVENT_CODES = frozenset(EventType.NOTIFICATION_LIFECYCLE_CODES)

# Screen family -> full tail regex (after splitting screen at its first slash).
# External payloads with a top-level url key use the 'url' destination directly.
ROUTE_FAMILIES = {
    'activity': r'',
    'devotional': r'[0-9]+',
    'fast': r'[0-9]+',
    'learn': r'(?:video|article|recipe)/[0-9]+',
    'prayer': r'[0-9]+',
    'prayer-request': r'[0-9]+',
    'prayer-requests': r'',
    'prayer-set': r'[0-9]+',
}


# v2 per-type payload keys, in addition to the common schema_version envelope.
# Client ingestion also accepts optional occurred_at and adds session/app/platform.
# Contracts only: producers must validate bounded vocabulary/identifiers, never
# free text, push tokens, message/data blobs, prayer content, IP or user agent.
NOTIFICATION_PAYLOAD_KEYS = {
    EventType.NOTIFICATION_PROMPT: frozenset({'stage', 'choice', 'trigger'}),
    EventType.NOTIFICATION_SEND: frozenset({
        'notif_id', 'kind', 'destination', 'platform', 'outcome', 'error_code', 'ticket_id', 'correlatable',
    }),
    EventType.NOTIFICATION_RECEIPT: frozenset({
        'notif_id', 'ticket_id', 'kind', 'outcome', 'receipt_status', 'error_code', 'lag_seconds',
    }),
    EventType.NOTIFICATION_OPENED: frozenset({
        'notif_id', 'entry', 'app_state', 'matched', 'kind',
    }),
    EventType.NOTIFICATION_DESTINATION: frozenset({
        'notif_id', 'destination', 'result', 'matched',
    }),
}
NOTIFICATION_KINDS = frozenset({
    'upcoming_fast', 'ongoing_fast', 'daily_fast', 'culmination_feast',
    'weekly_prayer_requests', 'prayer_request_completed', 'prayer_thanks',
    'fast_milestone', 'nudge_nonjoin', 'nudge_inactive',
    'nudge_activity_feed', 'nudge_prayer_acceptance', 'admin_manual', 'test', 'unclassified',
})
NOTIFICATION_DESTINATIONS = frozenset({
    'fast', 'devotional', 'prayer_set', 'prayer_request', 'prayer_requests',
    'learn', 'activity', 'prayer', 'url', 'none', 'other',
})
NOTIFICATION_OUTCOMES = frozenset({'provider_accepted', 'provider_rejected', 'send_error'})
NOTIFICATION_REPORTING_GROUPS = {
    kind: ('prayer_request_notifications' if kind in {'prayer_thanks', 'prayer_request_completed'}
           else kind)
    for kind in sorted(NOTIFICATION_KINDS)
}


MEANINGFUL_EVENT_CODES = frozenset({
    EventType.CHECKLIST_USED, EventType.PRAYER_SET_VIEWED, EventType.DEVOTIONAL_VIEWED,
    EventType.USER_JOINED_FAST, EventType.PRAYER_REQUEST_ACCEPTED,
})

ACTION_EVENT_CODES = frozenset({
    EventType.CHECKLIST_USED, EventType.USER_JOINED_FAST, EventType.USER_LEFT_FAST,
    EventType.USER_LOGGED_IN, EventType.USER_LOGGED_OUT, EventType.USER_ACCOUNT_CREATED,
    EventType.FAST_CREATED, EventType.FAST_UPDATED, EventType.ARTICLE_PUBLISHED,
    EventType.RECIPE_PUBLISHED, EventType.VIDEO_PUBLISHED, EventType.PRAYER_REQUEST_CREATED,
    EventType.PRAYER_REQUEST_ACCEPTED, EventType.PRAYER_REQUEST_COMPLETED,
    EventType.PRAYER_REQUEST_THANKS_SENT,
})
RETURN_SIGNAL_EVENT_CODES = frozenset({EventType.PRAYER_SET_VIEWED, EventType.DEVOTIONAL_VIEWED})
EXPOSURE_NOISE_EVENT_CODES = NOTIFICATION_LIFECYCLE_EVENT_CODES | frozenset({
    EventType.SCREEN_VIEW, EventType.APP_OPEN, EventType.SESSION_START, EventType.SESSION_END,
    EventType.PRAYER_VIEWED, EventType.PRAYER_REQUEST_VIEWED, EventType.TUTORIAL_VIDEO_VIEWED,
    EventType.FAST_BEGINNING, EventType.FAST_ENDING, EventType.DEVOTIONAL_AVAILABLE,
    EventType.FAST_PARTICIPANT_MILESTONE, EventType.USER_MILESTONE_REACHED,
})

EVENT_CODE_CLASSIFICATION = {
    **dict.fromkeys(sorted(ACTION_EVENT_CODES), 'action'),
    **dict.fromkeys(sorted(RETURN_SIGNAL_EVENT_CODES), 'return-signal'),
    **dict.fromkeys(sorted(EXPOSURE_NOISE_EVENT_CODES), 'exposure-noise'),
}

# track_in_analytics is informational; lifecycle exclusion uses the code list.


NOTIFICATION_PROMPT_CHOICES = {
    'shown': (None,), 'selected': ('enable', 'not_now'),
    'os_result': ('granted', 'denied', 'undetermined'),
}
NOTIFICATION_PROMPT_TRIGGERS = frozenset({'first_prayer', 'first_fast_join', 'settings', 'other'})
NOTIFICATION_OPEN_ENTRIES = frozenset({'tap', 'cold_start'})
NOTIFICATION_APP_STATES = frozenset({'cold', 'warm'})
NOTIFICATION_DESTINATION_RESULTS = frozenset({'opened', 'unresolved', 'rejected'})


def validate_notification_telemetry(body):
    """Return allowlisted data only; ValueError describes a bounded validation error."""
    if not isinstance(body, dict):
        raise ValueError('Expected an object')

    def allowed(key, values):
        return isinstance(body.get(key), str) and body[key] in values

    data = {'source': 'client_ui'}
    event = body.get('event')
    if event == 'prompt':
        if (not allowed('stage', NOTIFICATION_PROMPT_CHOICES)
                or body.get('choice') not in NOTIFICATION_PROMPT_CHOICES[body['stage']]
                or not allowed('trigger', NOTIFICATION_PROMPT_TRIGGERS)):
            raise ValueError('Invalid prompt')
        data.update(stage=body['stage'], trigger=body['trigger'])
        if body['stage'] != 'shown':
            data['choice'] = body['choice']
        code = EventType.NOTIFICATION_PROMPT
    elif event in ('opened', 'destination'):
        notif_id = body.get('notif_id')
        if not isinstance(notif_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16}', notif_id):
            raise ValueError('Invalid notif_id')
        data['notif_id'] = notif_id
        if event == 'opened':
            if (not allowed('entry', NOTIFICATION_OPEN_ENTRIES)
                    or not allowed('app_state', NOTIFICATION_APP_STATES)
                    or (body['entry'] == 'cold_start' and body['app_state'] == 'warm')):
                raise ValueError('Invalid open')
            data.update(entry=body['entry'], app_state=body['app_state'])
            code = EventType.NOTIFICATION_OPENED
        else:
            if (not allowed('destination', NOTIFICATION_DESTINATIONS)
                    or not allowed('result', NOTIFICATION_DESTINATION_RESULTS)
                    or (body['destination'] == 'none' and body['result'] == 'opened')):
                raise ValueError('Invalid destination')
            data.update(destination=body['destination'], result=body['result'])
            code = EventType.NOTIFICATION_DESTINATION
    else:
        raise ValueError('Invalid event')
    if 'occurred_at' in body:
        value = body['occurred_at']
        if not isinstance(value, str) or not re.fullmatch(
                r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                r'(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', value):
            raise ValueError('Invalid occurred_at')
        try:
            occurred = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Invalid occurred_at') from None
        now = timezone.now()
        if not now - timedelta(hours=24) <= occurred <= now + timedelta(minutes=5):
            raise ValueError('Invalid occurred_at')
        data['occurred_at'] = occurred.astimezone(datetime_timezone.utc).isoformat()
    return code, data
