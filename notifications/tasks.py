from celery import shared_task
from notifications.utils import send_push_notification
from hub.models import Fast
from hub.models import Profile
from hub.models import Day
from django.utils import timezone
from datetime import timedelta
from hub.models import User
from django.db.models import OuterRef, Subquery
import logging
from .constants import DAILY_FAST_MESSAGE, UPCOMING_FAST_MESSAGE, ONGOING_FAST_MESSAGE
from .utils import is_weekly_fast
from .models import PromoEmail
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.urls import reverse
from django.core.signing import TimestampSigner


logger = logging.getLogger(__name__)


# TODO: These tasks are not functional if the app has more than one church with active fasts.
# We need to update the tasks to send notifications to all churches when there are multiple churches.

@shared_task
def send_push_notification_task(message, data=None, tokens=None, notification_type=None):
    send_push_notification(message, data, tokens, notification_type)

@shared_task
def send_promo_email_task(promo_id):
    """
    Send a promotional email to all eligible recipients based on the email's targeting options.
    
    Args:
        promo_id: ID of the PromoEmail to send
    """
    try:
        promo = PromoEmail.objects.get(id=promo_id)
        
        # Update status to sending
        promo.status = PromoEmail.SENDING
        promo.save()
        
        # Get eligible recipients
        if promo.specific_emails:
            # If specific emails are provided, use those instead of filters
            users = User.objects.filter(email__in=promo.valid_specific_emails)
        else:
            # Get eligible recipients based on targeting options
            profiles = Profile.objects.all()
            
            # Apply filters
            if not promo.all_users:
                if promo.church_filter:
                    profiles = profiles.filter(church=promo.church_filter)
                
                if promo.joined_fast:
                    profiles = profiles.filter(fasts=promo.joined_fast)
            
            if promo.exclude_unsubscribed:
                profiles = profiles.filter(receive_promotional_emails=True)
            
            # Get users from profiles
            users = User.objects.filter(profile__in=profiles).distinct()
        
        if not users:
            logger.warning(f"No eligible recipients found for promotional email: {promo.title}")
            promo.status = PromoEmail.FAILED
            promo.save()
            return
        
        # Prepare email content
        from_email = f"Fast and Pray <{settings.EMAIL_HOST_USER}>"
        
        # Track success and failure counts
        success_count = 0
        failure_count = 0
        
        # Create signer for unsubscribe tokens
        signer = TimestampSigner()
        
        # Send to each user
        for user in users:
            try:
                # Create unsubscribe URL for this user
                unsubscribe_token = signer.sign(str(user.id))
                unsubscribe_url = f"{settings.BACKEND_URL}{reverse('notifications:unsubscribe')}?token={unsubscribe_token}"
                
                # Prepare email content with user context
                context = {
                    'user': user,
                    'title': promo.title,
                    'email_content': promo.content_html,
                    'unsubscribe_url': unsubscribe_url,
                    'site_url': settings.FRONTEND_URL
                }
                
                html_content = render_to_string('email/promotional_email.html', context)
                text_content = promo.content_text or strip_tags(html_content)
                
                # Create and send email
                email = EmailMultiAlternatives(
                    promo.subject,
                    text_content,
                    from_email,
                    [user.email]
                )
                email.attach_alternative(html_content, "text/html")
                email.send()
                
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to send promotional email to {user.email}: {str(e)}")
                failure_count += 1
        
        # Update promo status
        if success_count > 0:
            promo.status = PromoEmail.SENT
            promo.sent_at = timezone.now()
            promo.save()
            logger.info(f"Successfully sent promotional email '{promo.title}' to {success_count} recipients. Failed: {failure_count}")
        else:
            promo.status = PromoEmail.FAILED
            promo.save()
            logger.error(f"Failed to send promotional email '{promo.title}' to any recipients")
            
    except PromoEmail.DoesNotExist:
        logger.error(f"Promotional email with ID {promo_id} not found")
    except Exception as e:
        logger.error(f"Error in send_promo_email_task: {str(e)}")
        try:
            promo = PromoEmail.objects.get(id=promo_id)
            promo.status = PromoEmail.FAILED
            promo.save()
        except:
            pass

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
