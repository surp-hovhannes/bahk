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

# WARNING: All message strings in this file use .replace() for template substitution,
# NOT str.format(). This prevents KeyError/IndexError crashes when user-provided
# values (titles, names, descriptions) contain { or } characters.
#
# If you add a new message template with placeholders, always use .replace()
# to populate them -- never str.format().

PRAYER_REQUEST_COMPLETED_MESSAGE = (
    'Your prayer request "{title}" has completed. Send a thank you to those who prayed for you!'
)
FAST_PARTICIPANT_MILESTONE_MESSAGE = (
    '{count} people are fasting together in the {fast_name}!'
)
FAST_NONJOIN_NUDGE_MESSAGE = 'Join {count} others participating in the {fast_name}'
ACTIVITY_FEED_NUDGE_MESSAGE = 'You have {count} updates waiting in your activity feed'
INACTIVE_FAST_MEMBER_MESSAGE = 'The {fast_name} is ongoing — come pray with your community'
PRAYER_NUDGE_SINGLE_MESSAGE = 'Don\'t forget to pray for "{title}" today'
PRAYER_NUDGE_MULTIPLE_MESSAGE = 'You have {count} prayer requests still waiting for your prayers'
