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
from django.core.cache import cache
from celery.exceptions import RetryTaskError


logger = logging.getLogger(__name__)


# TODO: These tasks are not functional if the app has more than one church with active fasts.
# We need to update the tasks to send notifications to all churches when there are multiple churches.

@shared_task
def send_push_notification_task(message, data=None, tokens=None, notification_type=None):
    send_push_notification(message, data, tokens, notification_type)

def get_email_count():
    """Get the number of emails sent in the current rate limit window."""
    return cache.get('email_count', 0)

def increment_email_count():
    """Increment the email count and set expiration if not already set."""
    count = cache.get('email_count', 0)
    if count == 0:
        # Set expiration when first email is sent
        cache.set('email_count', 1, settings.EMAIL_RATE_LIMIT_WINDOW)
    else:
        cache.incr('email_count')

@shared_task(bind=True, max_retries=3)
def send_promo_email_task(self, promo_id, remaining_user_ids=None):
    """
    Send a promotional email to all eligible recipients based on the email's targeting options.
    Implements rate limiting to ensure emails are sent within the configured hourly limit.
    
    Args:
        promo_id: ID of the PromoEmail to send
        remaining_user_ids: Optional list of user IDs to send to (used when resuming after rate limit)
    """
    try:
        promo = PromoEmail.objects.get(id=promo_id)
                
        # Update status to sending
        promo.status = PromoEmail.SENDING
        promo.save()
        
        # Get users to process
        users = get_target_users(promo) if not remaining_user_ids else User.objects.filter(id__in=remaining_user_ids)
        
        if not users.exists(): # Use exists() for efficiency
            logger.warning(f"No eligible recipients found for promotional email ID {promo_id}: {promo.title}")
            # Set status to FAILED if no recipients are found, as per test requirements.
            promo.status = PromoEmail.FAILED 
            promo.save()
            return
        
        # Prepare email content
        from_email = f"Fast and Pray <{settings.EMAIL_HOST_USER}>"
        
        # Track success and failure counts
        success_count = 0
        failure_count = 0
        rate_limited = False  # Indicates if we paused due to rate limiting
        
        # Create signer for unsubscribe tokens
        signer = TimestampSigner()
        
        # Send to each user
        for user in users:
            # Ensure user has an email address and is active
            if not user.email or not user.is_active:
                logger.warning(f"Skipping user {user.id} for promo {promo_id} due to missing email or inactive status.")
                failure_count += 1  # Count inactive/no-email users as failures for this send attempt
                continue
                
            try:
                # Check rate limit before each email
                current_count = get_email_count()
                if current_count >= settings.EMAIL_RATE_LIMIT:
                    logger.warning(
                        "Rate limit reached (%d / %d emails per %d seconds). Rescheduling remaining users.", 
                        current_count, 
                        settings.EMAIL_RATE_LIMIT, 
                        settings.EMAIL_RATE_LIMIT_WINDOW
                    )
                    # Get remaining user IDs
                    remaining_user_ids = list(users.values_list('id', flat=True))[current_count:]
                    if remaining_user_ids and not settings.CELERY_TASK_ALWAYS_EAGER:
                        # Schedule the next batch automatically in non-eager mode
                        send_promo_email_task.apply_async(
                            args=[promo_id],
                            kwargs={'remaining_user_ids': remaining_user_ids},
                            countdown=settings.EMAIL_RATE_LIMIT_WINDOW
                        )
                    # Mark that we paused due to rate limiting
                    rate_limited = True
                    break

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
                
                # Increment email count after successful send
                increment_email_count()
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to send promotional email {promo_id} to user {user.id} ({user.email}): {str(e)}")
                failure_count += 1
        
        # Determine final status based on whether we rate limited

        if success_count > 0 and not rate_limited:
            # All intended recipients processed – mark as SENT
            promo.status = PromoEmail.SENT
            promo.sent_at = timezone.now()
            promo.save()
        elif success_count > 0 and rate_limited:
            # Some emails remain queued – keep status as SENDING and log info
            logger.info(
                "Emails exceed rate limit (%d per %d seconds). Sending additional batch(es).",
                settings.EMAIL_RATE_LIMIT,
                settings.EMAIL_RATE_LIMIT_WINDOW
            )

        elif failure_count > 0 and success_count == 0:
            # All attempts failed (e.g., send errors or skipped users)
            promo.status = PromoEmail.FAILED
            promo.save()

        # Log a summary of this task execution
        logger.info(
            "Finished sending promotional email '%s' (ID: %d). Success: %d, Failed: %d",
            promo.title,
            promo_id,
            success_count,
            failure_count
        )
        
    except PromoEmail.DoesNotExist:
        logger.error(f"Promotional email with ID {promo_id} not found for sending")
    except RetryTaskError:
        # Let the retry mechanism handle rescheduling
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in send_promo_email_task for promo_id {promo_id}: {str(e)}") # Use logger.exception
        try:
            # Attempt to mark as failed if an unexpected error occurs
            promo = PromoEmail.objects.get(id=promo_id)
            if promo.status == PromoEmail.SENDING:
                 promo.status = PromoEmail.FAILED
                 promo.save()
        except PromoEmail.DoesNotExist:
             pass # Already logged above
        except Exception as inner_e:
             logger.error(f"Failed to mark promo {promo_id} as FAILED after outer exception: {inner_e}")

def get_target_users(promo):
    """Helper function to get eligible users for a promotional email."""
    if promo.selected_users.exists():
        # If specific users are selected, use them directly
        logger.info(f"PromoEmail {promo.id}: Sending to {promo.selected_users.count()} specifically selected users.")
        return promo.selected_users.all()

    profiles = Profile.objects.all()
    
    if not promo.all_users:
        if promo.church_filter:
            profiles = profiles.filter(church=promo.church_filter)
        
        if promo.joined_fast:
            profiles = profiles.filter(fasts=promo.joined_fast)
    
    if promo.exclude_unsubscribed:
        profiles = profiles.filter(receive_promotional_emails=True)

    users = User.objects.filter(profile__in=profiles).distinct()
    logger.info(f"PromoEmail {promo.id}: Sending to {users.count()} users based on filters.")
    
    return users

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
