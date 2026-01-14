NOTIFICATION_TYPE_FILTERS = {
    'upcoming_fast': 'receive_upcoming_fast_push_notifications',
    'ongoing_fast': 'receive_ongoing_fast_push_notifications',
    'daily_fast': 'receive_daily_fast_push_notifications',
    'weekly_prayer_requests': 'receive_weekly_prayer_request_push_notifications',
}

WEEKLY_FAST_NAMES = ['friday', 'wednesday']

DAILY_FAST_MESSAGE = "Today is a fast day! Let's fast and pray together!"
UPCOMING_FAST_MESSAGE = "The {fast_name} is starting soon!"
ONGOING_FAST_MESSAGE = "Fast together today for the {fast_name}"
ONGOING_FAST_WITH_DEVOTIONAL_MESSAGE = "{devotional_title}. Tap to listen."
WEEKLY_PRAYER_REQUEST_MESSAGE = (
    "There {verb} {count} prayer request{plural_suffix} you can participate in this week. "
    "Join in or share one of your own"
)
