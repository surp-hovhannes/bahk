from celery import shared_task
from notifications.utils import send_push_notification
from hub.models import Fast
from hub.models import Profile
from hub.models import Day
from django.utils import timezone
from datetime import timedelta
from hub.models import User
from django.db.models import OuterRef, Subquery, Q
import logging
from .constants import DAILY_FAST_MESSAGE, UPCOMING_FAST_MESSAGE, ONGOING_FAST_MESSAGE
from .utils import is_weekly_fast


logger = logging.getLogger(__name__)


# TODO: These tasks are not functional if the app has more than one church with active fasts.
# We need to update the tasks to send notifications to all churches when there are multiple churches.

@shared_task
def send_push_notification_task(message, data=None, tokens=None, notification_type=None):
    send_push_notification(message, data, tokens, notification_type)

@shared_task
def send_upcoming_fast_push_notification_task():
    tomorrow = timezone.now().date() + timedelta(days=2)
    three_days_from_now = tomorrow + timedelta(days=3)

    # Subquery to find the next fast for each profile within the next three days
    next_fast_subquery = Day.objects.filter(
        fast__profiles__id=OuterRef('pk'),
        date__gt=tomorrow,
        date__lt=three_days_from_now
    ).order_by('date').values('fast__id')[:1]

    # Filter profiles based on the subquery
    profiles = Profile.objects.annotate(
        next_fast_id=Subquery(next_fast_subquery)
    ).filter(next_fast_id__isnull=False)

    # Get the upcoming fasts
    upcoming_fasts = Fast.objects.filter(id__in=profiles.values('next_fast_id'))

    if upcoming_fasts:
        upcoming_fast_to_display = upcoming_fasts[0]
        # Get the users associated with the upcoming fasts
        users_to_notify = User.objects.filter(profile__in=profiles)
        # if upcoming fast is a weekly fast, only include users who have turned on weekly fast notifications
        if is_weekly_fast(upcoming_fast_to_display):
            users_to_notify = users_to_notify.filter(profile__include_weekly_fasts_in_notifications=True)

        message = UPCOMING_FAST_MESSAGE
        data = {
            "fast_id": upcoming_fast_to_display.id,
            "fast_name": upcoming_fast_to_display.name,
        }

        if len(users_to_notify) > 0:
            send_push_notification_task(message, data, users_to_notify, 'upcoming_fast')
            logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for upcoming fasts')
        else:
            logger.info("Push Notification: No users to notify for upcoming fasts")
    else:
        logger.info("Push Notification: No upcoming fasts found")

@shared_task
def send_ongoing_fast_push_notification_task():
    # query ongoing fasts
    ongoing_fasts = Fast.objects.filter(days__date=timezone.now().date())

    if ongoing_fasts:
        ongoing_fast_to_display = ongoing_fasts[0] 
        # filter users who are joined to ongoing fasts
        users_to_notify = User.objects.filter(profile__fasts__in=ongoing_fasts).distinct()
        # if fast is a weekly fast, only include users who have turned on weekly fast notifications
        if is_weekly_fast(ongoing_fast_to_display):
            users_to_notify = users_to_notify.filter(profile__include_weekly_fasts_in_notifications=True)
            
        # send push notification to each user
        message = ONGOING_FAST_MESSAGE
        data = {
            "fast_id": ongoing_fast_to_display.id,
            "fast_name": ongoing_fast_to_display.name,
        }

        if len(users_to_notify) > 0:    
            send_push_notification_task(message, data, users_to_notify, 'ongoing_fast')
            logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for ongoing fasts')
        else:
            logger.info("Push Notification: No users to notify for ongoing fasts")
    else:
        logger.info("Push Notification: No ongoing fasts found")

@shared_task
def send_daily_fast_push_notification_task():
    # query today's fast
    today = Day.objects.filter(date=timezone.now().date()).first()
    if today:
        today_fast = today.fast
        # filter users who are joined to today's fast
        users_to_notify = User.objects.filter(profile__church=today_fast.church).distinct()
        # if fast is a weekly fast, only include users who have turned on weekly fast notifications
        if is_weekly_fast(today_fast):
            users_to_notify = users_to_notify.filter(profile__include_weekly_fasts_in_notifications=True)

        # send push notification to each user
        message = DAILY_FAST_MESSAGE
        data = {
            "fast_id": today_fast.id,
            "fast_name": today_fast.name,
        }

        if len(users_to_notify) > 0:
            send_push_notification_task(message, data, users_to_notify, 'daily_fast')
            logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for daily fasts')
        else:
            logger.info("Push Notification: No users to notify for daily fasts")
    else:
        logger.info("Push Notification: No daily fasts found")
