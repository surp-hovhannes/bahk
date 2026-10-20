"""Read-time analytics semantics; these classifications never rewrite stored rows."""

from .models import EventType

EVENT_SCHEMA_VERSION = 2

NOTIFICATION_LIFECYCLE_EVENT_CODES = frozenset({
    EventType.NOTIFICATION_PROMPT, EventType.NOTIFICATION_SEND,
    EventType.NOTIFICATION_RECEIPT, EventType.NOTIFICATION_OPENED,
    EventType.NOTIFICATION_DESTINATION,
})

# v2 per-type payload keys, in addition to the common schema_version envelope.
# Contracts only: producers must validate bounded vocabulary/identifiers, never
# free text, push tokens, message/data blobs, prayer content, IP or user agent.
NOTIFICATION_PAYLOAD_KEYS = {
    EventType.NOTIFICATION_PROMPT: frozenset({'stage', 'choice', 'trigger'}),
    EventType.NOTIFICATION_SEND: frozenset({
        'notif_id', 'kind', 'destination', 'platform', 'outcome', 'error_code', 'ticket_id',
    }),
    EventType.NOTIFICATION_RECEIPT: frozenset({
        'notif_id', 'ticket_id', 'receipt_status', 'error_code', 'lag_seconds',
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
    'fast_milestone', 'post_fast', 'nudge_nonjoin', 'nudge_inactive',
    'nudge_activity_feed', 'nudge_prayer_acceptance', 'admin_manual', 'test', 'unclassified',
})
NOTIFICATION_DESTINATIONS = frozenset({
    'fast', 'devotional', 'prayer_set', 'prayer_request', 'prayer_requests',
    'learn', 'none', 'other',
})
NOTIFICATION_OUTCOMES = frozenset({'provider_accepted', 'provider_rejected', 'send_error'})
NOTIFICATION_REPORTING_GROUPS = {
    kind: ('prayer_request_notifications' if kind in {'prayer_thanks', 'prayer_request_completed'}
           else kind)
    for kind in NOTIFICATION_KINDS
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
    **dict.fromkeys(ACTION_EVENT_CODES, 'action'),
    **dict.fromkeys(RETURN_SIGNAL_EVENT_CODES, 'return-signal'),
    **dict.fromkeys(EXPOSURE_NOISE_EVENT_CODES, 'exposure-noise'),
}
