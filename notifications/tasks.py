from celery import shared_task
from notifications.utils import send_push_notification
from hub.models import Fast, Devotional
from hub.models import Profile
from hub.models import Day
from django.utils import timezone
from datetime import timedelta
from hub.models import User
from django.db.models import OuterRef, Subquery
import logging
import time
from .constants import DAILY_FAST_MESSAGE, UPCOMING_FAST_MESSAGE, ONGOING_FAST_MESSAGE, ONGOING_FAST_WITH_DEVOTIONAL_MESSAGE
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
def send_promo_email_task(self, promo_id, batch_start_index=0):
    """
    Send a promotional email to all eligible recipients based on the email's targeting options.
    Implements rate limiting to ensure emails are sent within the configured hourly limit.
    
    Args:
        promo_id: ID of the PromoEmail to send
        batch_start_index: Index to start processing from (for batch continuation)
    """
    # Create a lock key for this specific promo to prevent concurrent execution
    lock_key = f'promo_task_lock:{promo_id}'
    lock_timeout = 3600  # 1 hour timeout
    
    # Try to acquire lock
    if cache.get(lock_key):
        logger.warning(f"Task for promo {promo_id} is already running, skipping duplicate execution")
        return
    
    # Set lock
    cache.set(lock_key, True, timeout=lock_timeout)
    
    try:
        promo = PromoEmail.objects.get(id=promo_id)
        
        # Check if another task already completed this promo
        if promo.status in [PromoEmail.SENT, PromoEmail.FAILED, PromoEmail.CANCELED]:
            logger.info(f"Promo {promo_id} already completed with status {promo.status}, skipping")
            return
                
        # Update status to sending only if it's not already sending
        if promo.status != PromoEmail.SENDING:
            promo.status = PromoEmail.SENDING
            promo.save()
        
        # Implement caching for user IDs
        cache_key = f'promo:{promo_id}:user_ids'
        user_ids = cache.get(cache_key)
        if user_ids is None:
            target_users = get_target_users(promo)
            if not target_users.exists():
                logger.warning(f"No eligible recipients found for promotional email ID {promo_id}: {promo.title}")
                # Set status to FAILED if no recipients are found, as per test requirements.
                promo.status = PromoEmail.FAILED 
                promo.save()
                return
            user_ids = list(target_users.values_list('id', flat=True))
            cache.set(cache_key, user_ids, timeout=86400)  # Cache for 24 hours
        
        # Get total users count 
        total_users = len(user_ids)
        
        # Validate batch_start_index
        if batch_start_index >= total_users:
            logger.warning(f"batch_start_index {batch_start_index} >= total_users {total_users}, marking as completed")
            promo.status = PromoEmail.SENT
            promo.sent_at = timezone.now()
            promo.save()
            cache.delete(cache_key)
            return
        
        # Get users for the current batch - don't slice, use the full list and iterate from batch_start_index
        users_to_process = User.objects.filter(id__in=user_ids[batch_start_index:]).order_by('id')
        
        # Prepare email content
        from_email = f"Fast and Pray <{settings.EMAIL_HOST_USER}>"
        
        # Track success and failure counts
        success_count = 0
        failure_count = 0
        rate_limited = False  # Indicates if we paused due to rate limiting
        processed_count = 0  # Track how many we've processed in this batch
        
        # Create signer for unsubscribe tokens
        signer = TimestampSigner()
        
        # Process users starting from batch_start_index
        for current_user_index, user in enumerate(users_to_process):
            # Calculate actual position in original user_ids list
            actual_index = batch_start_index + current_user_index
            
            # Ensure user has an email address and is active
            if not user.email or not user.is_active:
                logger.warning(f"Skipping user {user.id} for promo {promo_id} due to missing email or inactive status.")
                failure_count += 1  # Count inactive/no-email users as failures for this send attempt
                processed_count += 1
                continue
                
            try:
                # Check rate limit before each email
                current_count = get_email_count()
                if current_count >= settings.EMAIL_RATE_LIMIT:
                    logger.warning(
                        "Rate limit reached (%d / %d emails per %d seconds). Processed %d/%d users. Rescheduling remaining users.", 
                        current_count, 
                        settings.EMAIL_RATE_LIMIT, 
                        settings.EMAIL_RATE_LIMIT_WINDOW,
                        actual_index,
                        total_users
                    )
                    # Calculate the correct next batch start index
                    next_batch_start = actual_index
                    if not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
                        # Schedule the next batch automatically in non-eager mode
                        send_promo_email_task.apply_async(
                            args=[promo_id],
                            kwargs={'batch_start_index': next_batch_start},
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
                
                # Add a configurable delay to respect API rate limits
                api_delay = getattr(settings, 'EMAIL_API_DELAY_SECONDS', 1.0)
                time.sleep(api_delay)
                
                email.send()
                
                # Increment email count after successful send
                increment_email_count()
                success_count += 1
                processed_count += 1
                
            except Exception as e:
                error_message = str(e)
                
                # Check if it's a Mailgun rate limit error
                if "420" in error_message or "429" in error_message or "rate limit" in error_message.lower():
                    logger.warning(f"Mailgun rate limit hit for user {user.id} ({user.email}): {error_message}")
                    # Calculate the correct next batch start index for Mailgun rate limit
                    next_batch_start = actual_index
                    remaining_users_count = total_users - actual_index
                    if remaining_users_count > 0 and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
                        logger.info(f"Rescheduling {remaining_users_count} remaining users due to Mailgun rate limit")
                        send_promo_email_task.apply_async(
                            args=[promo_id],
                            kwargs={'batch_start_index': next_batch_start},
                            countdown=7200  # Wait 2 hours before retrying when hitting Mailgun limits
                        )
                    rate_limited = True
                    break
                else:
                    logger.error(f"Failed to send promotional email {promo_id} to user {user.id} ({user.email}): {error_message}")
                    failure_count += 1
                    processed_count += 1
        
        # Determine final status based on whether we rate limited
        if not rate_limited and (batch_start_index + processed_count) >= total_users:
            # All intended recipients processed
            if success_count == 0 and failure_count > 0:
                # All attempts failed (e.g., send errors or skipped users)
                promo.status = PromoEmail.FAILED
                promo.save()
            else:
                # At least some succeeded - mark as SENT
                promo.status = PromoEmail.SENT
                promo.sent_at = timezone.now()
                promo.save()
            # Clean up cache after completion
            cache.delete(cache_key)
            logger.info(
                "Completed sending promotional email '%s' (ID: %d). Total Success: %d, Total Failed: %d",
                promo.title,
                promo_id,
                success_count,
                failure_count
            )
        elif rate_limited:
            # Some emails remain queued – keep status as SENDING and log info
            logger.info(
                "Rate limit reached for '%s' (ID: %d). Processed %d/%d users in this batch. Remaining emails queued for next batch.",
                promo.title,
                promo_id,
                batch_start_index + processed_count,
                total_users
            )

        # Log a summary of this batch execution
        logger.info(
            "Batch completed for promotional email '%s' (ID: %d). Batch Success: %d, Batch Failed: %d, Processed: %d/%d",
            promo.title,
            promo_id,
            success_count,
            failure_count,
            batch_start_index + processed_count,
            total_users
        )
        
    except PromoEmail.DoesNotExist:
        logger.error(f"Promotional email with ID {promo_id} not found for sending")
    except RetryTaskError:
        # Let the retry mechanism handle rescheduling
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in send_promo_email_task for promo_id {promo_id}: {str(e)}") # Use logger.exception
        # Clean up cache in exception handlers
        cache_key = f'promo:{promo_id}:user_ids'
        cache.delete(cache_key)
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
    finally:
        # Always release the lock
        cache.delete(lock_key)

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

        message = UPCOMING_FAST_MESSAGE.format(fast_name=upcoming_fast_to_display.name)
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
            
        # Check if there's a devotional for today
        today_devotional = Devotional.objects.filter(
            day__date=timezone.now().date(),
            day__fast=ongoing_fast_to_display
        ).first()
        
        # Choose message based on whether there's a devotional
        if today_devotional and today_devotional.video and today_devotional.video.title:
            message = ONGOING_FAST_WITH_DEVOTIONAL_MESSAGE.format(
                fast_name=ongoing_fast_to_display.name,
                devotional_title=today_devotional.video.title
            )
            data = {
                "fast_id": ongoing_fast_to_display.id,
                "fast_name": ongoing_fast_to_display.name
            }
        else:
            message = ONGOING_FAST_MESSAGE.format(fast_name=ongoing_fast_to_display.name)
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
    if not today:
        # Log and return
        logger.info("Push Notification: No Day entry found for today")
        return
        
    today_fast = today.fast
    if not today_fast:
        # Log and return
        logger.info("Push Notification: Day exists but has no associated Fast")
        return
        
    # filter users who are joined to today's fast
    users_to_notify = User.objects.filter(profile__church=today_fast.church).distinct()
    # if fast is a weekly fast, only include users who have turned on weekly fast notifications
    if is_weekly_fast(today_fast):
        users_to_notify = users_to_notify.filter(profile__include_weekly_fasts_in_notifications=True)

    # send push notification to each user
    message = DAILY_FAST_MESSAGE.format(fast_name=today_fast.name)
    data = {
        "fast_id": today_fast.id,
        "fast_name": today_fast.name,
    }

    if len(users_to_notify) > 0:
        send_push_notification_task(message, data, users_to_notify, 'daily_fast')
        logger.info(f'Push Notification: Fast reminder sent to {len(users_to_notify)} users for daily fasts')
    else:
        logger.info("Push Notification: No users to notify for daily fasts")
