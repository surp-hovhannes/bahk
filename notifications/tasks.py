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
        # Get the users associated with the upcoming fasts
        users_to_notify = User.objects.filter(profile__in=profiles)

        message = "You have joined a fast starting soon!"
        data = {
            "fast_id": upcoming_fasts[0].id,
            "fast_name": upcoming_fasts[0].name,
        }
        send_push_notification_task(message, data, users_to_notify, 'upcoming_fast')
        logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for upcoming fasts')
    else:
        logger.info("Push Notification: No upcoming fasts found")

@shared_task
def send_ongoing_fast_push_notification_task():
    # query ongoing fasts
    ongoing_fasts = Fast.objects.filter(days__date=timezone.now().date())

    if ongoing_fasts:   
        # filter users who are joined to ongoing fasts
        users_to_notify = User.objects.filter(profile__fasts__in=ongoing_fasts).distinct()
        # send push notification to each user
        message = "You have joined a fast that is currently ongoing! Lets fast and pray together!"
        data = {
            "fast_id": ongoing_fasts[0].id,
            "fast_name": ongoing_fasts[0].name,
        }
        send_push_notification_task(message, data, users_to_notify, 'ongoing_fast')
        logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for ongoing fasts')
    else:
        logger.info("Push Notification: No ongoing fasts found")

@shared_task
def send_daily_fast_push_notification_task():
    # query today's fast
    today_fast = Day.objects.filter(date=timezone.now().date()).first()
    if today_fast:
        # filter users who are joined to today's fast
        users_to_notify = User.objects.filter(profile__church=today_fast.fast.church).distinct()
        # send push notification to each user
        message = "Today is a fast day! Join us in fasting and praying together!"
        data = {
            "fast_id": today_fast.fast.id,
            "fast_name": today_fast.fast.name,
        }
        send_push_notification_task(message, data, users_to_notify, 'daily_fast')
        logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for daily fasts')
    else:
        logger.info("Push Notification: No daily fasts found")
