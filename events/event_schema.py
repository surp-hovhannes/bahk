"""Read-time analytics semantics; these classifications never rewrite stored rows."""

EVENT_SCHEMA_VERSION = 2

MEANINGFUL_EVENT_CODES = frozenset({
    'checklist_used', 'prayer_set_viewed', 'devotional_viewed',
    'user_joined_fast', 'prayer_request_accepted',
})

ACTION_EVENT_CODES = frozenset({
    'checklist_used', 'user_joined_fast', 'user_left_fast',
    'user_logged_in', 'user_logged_out', 'user_account_created',
    'fast_created', 'fast_updated', 'article_published', 'recipe_published',
    'video_published', 'prayer_request_created', 'prayer_request_accepted',
    'prayer_request_completed', 'prayer_request_thanks_sent',
})
RETURN_SIGNAL_EVENT_CODES = frozenset({'prayer_set_viewed', 'devotional_viewed'})
EXPOSURE_NOISE_EVENT_CODES = frozenset({
    'screen_view', 'app_open', 'session_start', 'session_end',
    'prayer_viewed', 'prayer_request_viewed', 'tutorial_video_viewed',
    'fast_beginning', 'fast_ending', 'devotional_available',
    'fast_participant_milestone', 'user_milestone_reached',
})

EVENT_CODE_CLASSIFICATION = {
    **dict.fromkeys(ACTION_EVENT_CODES, 'action'),
    **dict.fromkeys(RETURN_SIGNAL_EVENT_CODES, 'return-signal'),
    **dict.fromkeys(EXPOSURE_NOISE_EVENT_CODES, 'exposure-noise'),
}
