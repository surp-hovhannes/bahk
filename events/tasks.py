"""
Celery tasks for activity feed operations.
"""

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
import logging

from .models import UserActivityFeed, Event

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def create_activity_feed_item_task(self, event_id, user_id=None):
    """
    Create an activity feed item from an event asynchronously.
    
    Args:
        event_id: ID of the Event to create feed item from
        user_id: Optional user ID (if not provided, uses event.user)
    """
    try:
        event = Event.objects.select_related('user', 'event_type', 'content_type').get(id=event_id)
        
        # Use provided user_id or event.user
        user = None
        if user_id:
            user = User.objects.get(id=user_id)
        elif event.user:
            user = event.user
        
        if not user:
            logger.info(f"No user found for event {event_id}, skipping feed item creation")
            return
        
        # Create the feed item
        feed_item = UserActivityFeed.create_from_event(event, user)
        
        if feed_item:
            logger.info(f"Created activity feed item {feed_item.id} for user {user.username}")
        else:
            logger.info(f"No feed item created for event {event_id} (not a tracked event type)")
            
    except Event.DoesNotExist:
        logger.error(f"Event {event_id} not found for feed item creation")
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for feed item creation")
    except Exception as exc:
        logger.error(f"Error creating activity feed item for event {event_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def create_fast_reminder_feed_items_task(self, fast_id, reminder_type='fast_reminder'):
    """
    Create fast reminder feed items for all users in a fast.
    
    Args:
        fast_id: ID of the Fast
        reminder_type: Type of reminder ('fast_reminder', 'devotional_reminder')
    """
    try:
        from hub.models import Fast
        fast = Fast.objects.get(id=fast_id)
        
        # Get all users in the fast
        users = User.objects.filter(profile__fasts=fast).distinct()
        
        created_count = 0
        for user in users:
            try:
                feed_item = UserActivityFeed.create_fast_reminder(user, fast, reminder_type)
                if feed_item:
                    created_count += 1
            except Exception as e:
                logger.error(f"Error creating reminder for user {user.id}: {e}")
                continue
        
        logger.info(f"Created {created_count} reminder feed items for fast {fast.name}")
        return created_count
        
    except Fast.DoesNotExist:
        logger.error(f"Fast {fast_id} not found for reminder feed items")
    except Exception as exc:
        logger.error(f"Error creating reminder feed items for fast {fast_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def create_devotional_reminder_feed_items_task(self, devotional_id, fast_id):
    """
    Create devotional reminder feed items for all users in a fast.
    
    Args:
        devotional_id: ID of the Devotional
        fast_id: ID of the Fast
    """
    try:
        from hub.models import Fast, Devotional
        fast = Fast.objects.get(id=fast_id)
        devotional = Devotional.objects.get(id=devotional_id)
        
        # Get all users in the fast
        users = User.objects.filter(profile__fasts=fast).distinct()
        
        created_count = 0
        for user in users:
            try:
                feed_item = UserActivityFeed.create_devotional_reminder(user, devotional, fast)
                if feed_item:
                    created_count += 1
            except Exception as e:
                logger.error(f"Error creating devotional reminder for user {user.id}: {e}")
                continue
        
        logger.info(f"Created {created_count} devotional reminder feed items for fast {fast.name}")
        return created_count
        
    except (Fast.DoesNotExist, Devotional.DoesNotExist) as e:
        logger.error(f"Fast {fast_id} or Devotional {devotional_id} not found: {e}")
    except Exception as exc:
        logger.error(f"Error creating devotional reminder feed items: {exc}")
        raise self.retry(exc=exc)


@shared_task
def batch_create_activity_feed_items_task(event_ids, user_id=None):
    """
    Create multiple activity feed items in batch.
    
    Args:
        event_ids: List of Event IDs
        user_id: Optional user ID (if not provided, uses each event's user)
    """
    try:
        events = Event.objects.select_related('user', 'event_type', 'content_type').filter(
            id__in=event_ids
        )
        
        created_count = 0
        for event in events:
            try:
                user = None
                if user_id:
                    user = User.objects.get(id=user_id)
                elif event.user:
                    user = event.user
                
                if user:
                    feed_item = UserActivityFeed.create_from_event(event, user)
                    if feed_item:
                        created_count += 1
                        
            except Exception as e:
                logger.error(f"Error creating feed item for event {event.id}: {e}")
                continue
        
        logger.info(f"Created {created_count} feed items from {len(events)} events")
        return created_count
        
    except Exception as e:
        logger.error(f"Error in batch feed item creation: {e}")
        return 0


@shared_task
def cleanup_old_activity_feed_items_task():
    """
    Clean up old activity feed items based on retention policies.
    This task can be scheduled to run regularly.
    """
    try:
        deleted_count = UserActivityFeed.cleanup_old_items(dry_run=False)
        logger.info(f"Cleaned up {deleted_count} old activity feed items")
        return deleted_count
    except Exception as e:
        logger.error(f"Error cleaning up old activity feed items: {e}")
        return 0


@shared_task
def populate_user_activity_feed_task(user_id, days_back=30):
    """
    Populate a user's activity feed with historical events.
    
    Args:
        user_id: ID of the User
        days_back: Number of days back to populate
    """
    try:
        user = User.objects.get(id=user_id)
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days_back)
        
        # Get user's events in the date range
        events = Event.objects.filter(
            user=user,
            timestamp__gte=start_date,
            timestamp__lte=end_date
        ).select_related('event_type', 'content_type')
        
        created_count = 0
        for event in events:
            # Check if feed item already exists
            existing_feed_item = UserActivityFeed.objects.filter(
                user=user,
                event=event
            ).exists()
            
            if not existing_feed_item:
                feed_item = UserActivityFeed.create_from_event(event, user)
                if feed_item:
                    created_count += 1
        
        logger.info(f"Created {created_count} activity feed items for user {user.username}")
        return created_count
        
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for feed population")
        return 0
    except Exception as e:
        logger.error(f"Error populating activity feed for user {user_id}: {e}")
        return 0 