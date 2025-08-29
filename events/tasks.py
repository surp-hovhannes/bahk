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


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def track_fast_participant_milestone_task(self, fast_id, participant_count=None, milestone_type="participant_count"):
    """
    Track when a fast reaches participation milestones asynchronously.
    
    Args:
        fast_id: ID of the Fast
        participant_count: Current number of participants (if None, will be calculated)
        milestone_type: Type of milestone (e.g., "participant_count", "percentage")
    """
    try:
        from hub.models import Fast
        from .signals import track_fast_participant_milestone
        
        fast = Fast.objects.get(id=fast_id)
        
        # Calculate participant count if not provided
        if participant_count is None:
            participant_count = fast.profiles.count()
        
        # Track the milestone
        milestone_created = track_fast_participant_milestone(fast, participant_count, milestone_type)
        
        if milestone_created:
            logger.info(f"Created milestone event for fast {fast.name} with {participant_count} participants")
        else:
            logger.info(f"No milestone reached for fast {fast.name} with {participant_count} participants")
        
        return {
            'fast_id': fast_id,
            'fast_name': fast.name,
            'participant_count': participant_count,
            'milestone_created': milestone_created
        }
        
    except Fast.DoesNotExist:
        logger.error(f"Fast {fast_id} not found for milestone tracking")
    except Exception as exc:
        logger.error(f"Error tracking fast participant milestone for fast {fast_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def track_fast_beginning_task(self, fast_id):
    """
    Track when a fast begins asynchronously.
    
    Args:
        fast_id: ID of the Fast
    """
    try:
        from hub.models import Fast
        from .signals import track_fast_beginning
        
        fast = Fast.objects.get(id=fast_id)
        
        # Track the fast beginning
        track_fast_beginning(fast)
        
        logger.info(f"Tracked fast beginning event for fast {fast.name}")
        
        return {
            'fast_id': fast_id,
            'fast_name': fast.name,
            'event_created': True
        }
        
    except Fast.DoesNotExist:
        logger.error(f"Fast {fast_id} not found for beginning tracking")
    except Exception as exc:
        logger.error(f"Error tracking fast beginning for fast {fast_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task
def check_fast_beginning_events_task():
    """
    Check for fasts that are beginning today and create beginning events.
    This task runs daily to ensure fast beginning events are tracked.
    """
    try:
        from hub.models import Fast
        from django.db.models import Min
        from django.utils import timezone
        
        today = timezone.now().date()
        
        # Find fasts that start today (first day of the fast)
        fasts_beginning_today = Fast.objects.annotate(
            start_date=Min('days__date')
        ).filter(
            start_date=today
        ).distinct()
        
        events_created = 0
        for fast in fasts_beginning_today:
            try:
                # Check if beginning event already exists for today
                from .models import Event, EventType
                existing_event = Event.objects.filter(
                    event_type__code=EventType.FAST_BEGINNING,
                    object_id=fast.id,
                    content_type__model='fast',
                    timestamp__date=today
                ).exists()
                
                if not existing_event:
                    # Create the beginning event
                    track_fast_beginning_task.delay(fast.id)
                    events_created += 1
                    logger.info(f"Scheduled fast beginning event for {fast.name}")
                else:
                    logger.info(f"Fast beginning event already exists for {fast.name} today")
                    
            except Exception as e:
                logger.error(f"Error processing fast beginning for {fast.name}: {e}")
                continue
        
        logger.info(f"Checked {fasts_beginning_today.count()} fasts, created {events_created} beginning events")
        return {
            'fasts_checked': fasts_beginning_today.count(),
            'events_created': events_created
        }
        
    except Exception as e:
        logger.error(f"Error checking fast beginning events: {e}")
        return {
            'fasts_checked': 0,
            'events_created': 0,
            'error': str(e)
        }


@shared_task
def check_participation_milestones_task():
    """
    Check all active fasts for participation milestones.
    This task runs daily to ensure milestones are tracked.
    """
    try:
        from hub.models import Fast
        from django.utils import timezone
        from .signals import check_and_track_participation_milestones
        
        today = timezone.now().date()
        
        # Find active fasts (those with days today or in the future)
        active_fasts = Fast.objects.filter(
            days__date__gte=today
        ).distinct()
        
        milestones_created = 0
        fasts_checked = 0
        
        for fast in active_fasts:
            try:
                fasts_checked += 1
                
                # Check for milestones
                milestone_created = check_and_track_participation_milestones(fast)
                
                if milestone_created:
                    milestones_created += 1
                    logger.info(f"Created milestone event for fast {fast.name}")
                else:
                    logger.debug(f"No milestone reached for fast {fast.name}")
                    
            except Exception as e:
                logger.error(f"Error checking milestones for fast {fast.name}: {e}")
                continue
        
        logger.info(f"Checked {fasts_checked} active fasts, created {milestones_created} milestone events")
        return {
            'fasts_checked': fasts_checked,
            'milestones_created': milestones_created
        }
        
    except Exception as e:
        logger.error(f"Error checking participation milestones: {e}")
        return {
            'fasts_checked': 0,
            'milestones_created': 0,
            'error': str(e)
        }


@shared_task
def check_devotional_availability_task():
    """
    Check for devotionals that become available today and create availability events.
    This task runs daily to ensure devotional availability events are tracked.
    """
    try:
        from hub.models import Devotional
        from django.utils import timezone
        from .signals import track_devotional_available
        
        today = timezone.now().date()
        
        # Find devotionals for today
        devotionals_today = Devotional.objects.filter(
            day__date=today
        ).select_related('day', 'day__fast', 'day__fast__church', 'video')
        
        logger.info(f"Found {devotionals_today.count()} devotionals for today ({today})")
        
        events_created = 0
        for devotional in devotionals_today:
            logger.info(f"Processing devotional {devotional.id}: {devotional.video.title if devotional.video else 'No video'}")
            try:
                fast = devotional.day.fast
                
                # Check if availability event already exists for today
                from .models import Event, EventType
                existing_event = Event.objects.filter(
                    event_type__code=EventType.DEVOTIONAL_AVAILABLE,
                    object_id=fast.id,
                    content_type__model='fast',
                    timestamp__date=today,
                    data__devotional_id=devotional.id
                ).exists()
                
                if not existing_event:
                    # Create devotional info
                    devotional_info = {
                        'devotional_id': devotional.id,
                        'devotional_title': devotional.video.title if devotional.video else 'Devotional',
                        'devotional_description': devotional.description or '',
                        'devotional_date': today.isoformat(),
                        'day_id': devotional.day.id,
                        'order': devotional.order,
                    }
                    
                    # Track the devotional availability
                    track_devotional_available(fast, devotional_info)
                    events_created += 1
                    logger.info(f"Created devotional availability event for {fast.name} - {devotional.video.title if devotional.video else 'Devotional'}")
                else:
                    logger.info(f"Devotional availability event already exists for {fast.name} - {devotional.video.title if devotional.video else 'Devotional'} today")
                    
            except Exception as e:
                logger.error(f"Error processing devotional availability for devotional {devotional.id}: {e}")
                continue
        
        logger.info(f"Checked {devotionals_today.count()} devotionals, created {events_created} availability events")
        return {
            'devotionals_checked': devotionals_today.count(),
            'events_created': events_created
        }
        
    except Exception as e:
        logger.error(f"Error checking devotional availability: {e}")
        return {
            'devotionals_checked': 0,
            'events_created': 0,
            'error': str(e)
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def track_devotional_availability_task(self, fast_id, devotional_id):
    """
    Track when a specific devotional becomes available asynchronously.
    
    Args:
        fast_id: ID of the Fast
        devotional_id: ID of the Devotional
    """
    try:
        from hub.models import Fast, Devotional
        from .signals import track_devotional_available
        
        fast = Fast.objects.get(id=fast_id)
        devotional = Devotional.objects.get(id=devotional_id)
        
        # Create devotional info
        devotional_info = {
            'devotional_id': devotional.id,
            'devotional_title': devotional.video.title if devotional.video else 'Devotional',
            'devotional_description': devotional.description or '',
            'devotional_date': devotional.day.date.isoformat(),
            'day_id': devotional.day.id,
            'order': devotional.order,
        }
        
        # Track the devotional availability
        track_devotional_available(fast, devotional_info)
        
        logger.info(f"Tracked devotional availability event for fast {fast.name} - {devotional.video.title if devotional.video else 'Devotional'}")
        
        return {
            'fast_id': fast_id,
            'fast_name': fast.name,
            'devotional_id': devotional_id,
            'devotional_title': devotional_info['devotional_title'],
            'event_created': True
        }
        
    except (Fast.DoesNotExist, Devotional.DoesNotExist) as e:
        logger.error(f"Fast {fast_id} or Devotional {devotional_id} not found: {e}")
    except Exception as exc:
        logger.error(f"Error tracking devotional availability for fast {fast_id}, devotional {devotional_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def track_article_published_task(self, article_id):
    """
    Track when an article is published asynchronously.
    
    Args:
        article_id: ID of the Article
    """
    try:
        from learning_resources.models import Article
        from .signals import track_article_published
        
        article = Article.objects.get(id=article_id)
        
        # Track the article publication
        track_article_published(article)
        
        logger.info(f"Tracked article publication event for {article.title}")
        
        return {
            'article_id': article_id,
            'article_title': article.title,
            'event_created': True
        }
        
    except Article.DoesNotExist:
        logger.error(f"Article {article_id} not found")
    except Exception as exc:
        logger.error(f"Error tracking article publication for article {article_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def track_recipe_published_task(self, recipe_id):
    """
    Track when a recipe is published asynchronously.
    
    Args:
        recipe_id: ID of the Recipe
    """
    try:
        from learning_resources.models import Recipe
        from .signals import track_recipe_published
        
        recipe = Recipe.objects.get(id=recipe_id)
        
        # Track the recipe publication
        track_recipe_published(recipe)
        
        logger.info(f"Tracked recipe publication event for {recipe.title}")
        
        return {
            'recipe_id': recipe_id,
            'recipe_title': recipe.title,
            'event_created': True
        }
        
    except Recipe.DoesNotExist:
        logger.error(f"Recipe {recipe_id} not found")
    except Exception as exc:
        logger.error(f"Error tracking recipe publication for recipe {recipe_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def track_video_published_task(self, video_id):
    """
    Track when a video is published asynchronously.
    Only tracks general and tutorial videos.
    
    Args:
        video_id: ID of the Video
    """
    try:
        from learning_resources.models import Video
        from .signals import track_video_published
        
        video = Video.objects.get(id=video_id)
        
        # Only track general and tutorial videos
        if video.category not in ['general', 'tutorial']:
            logger.info(f"Skipping video publication tracking for {video.title} (category: {video.category})")
            return {
                'video_id': video_id,
                'video_title': video.title,
                'event_created': False,
                'reason': f'Category {video.category} not tracked'
            }
        
        # Track the video publication
        track_video_published(video)
        
        logger.info(f"Tracked video publication event for {video.title}")
        
        return {
            'video_id': video_id,
            'video_title': video.title,
            'video_category': video.category,
            'event_created': True
        }
        
    except Video.DoesNotExist:
        logger.error(f"Video {video_id} not found")
    except Exception as exc:
        logger.error(f"Error tracking video publication for video {video_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc)


@shared_task
def check_completed_fast_milestones_task():
    """
    Check for users who have completed their first non-weekly fast and award milestones.
    This task runs daily to check for fasts that ended yesterday.
    """
    try:
        from hub.models import Fast, Profile
        from django.utils import timezone
        from datetime import timedelta
        from django.db import models
        from .models import UserMilestone
        from notifications.utils import is_weekly_fast
        
        yesterday = timezone.now().date() - timedelta(days=1)
        logger.info(f"Checking for fasts that ended on {yesterday}")
        
        # Find fasts that ended yesterday
        completed_fasts = Fast.objects.filter(
            days__date=yesterday
        ).annotate(
            end_date=models.Max('days__date')
        ).filter(
            end_date=yesterday  # Only fasts where yesterday was truly the last day
        ).distinct()
        
        milestones_awarded = 0
        users_processed = 0
        
        for fast in completed_fasts:
            # Skip weekly fasts
            if is_weekly_fast(fast):
                logger.debug(f"Skipping weekly fast: {fast.name}")
                continue
                
            logger.info(f"Processing completed fast: {fast.name}")
            
            # Get all users who participated in this fast
            participants = fast.profiles.all()
            
            for profile in participants:
                try:
                    users_processed += 1
                    user = profile.user
                    
                    # Check if user already has the first non-weekly fast completion milestone
                    if UserMilestone.objects.filter(
                        user=user,
                        milestone_type='first_nonweekly_fast_complete'
                    ).exists():
                        continue
                    
                    # Check if this is their first completed non-weekly fast
                    # Get all fasts this user has participated in that have ended before today
                    user_completed_fasts = Fast.objects.filter(
                        profiles=profile,
                        days__date__lt=timezone.now().date()
                    ).annotate(
                        end_date=models.Max('days__date')
                    ).filter(
                        end_date__lt=timezone.now().date()
                    ).distinct()
                    
                    # Filter out weekly fasts
                    non_weekly_completed_fasts = [
                        f for f in user_completed_fasts 
                        if not is_weekly_fast(f)
                    ]
                    
                    # If this is their first completed non-weekly fast, award milestone
                    if len(non_weekly_completed_fasts) == 1 and fast in non_weekly_completed_fasts:
                        milestone = UserMilestone.create_milestone(
                            user=user,
                            milestone_type='first_nonweekly_fast_complete',
                            related_object=fast,
                            data={
                                'fast_id': fast.id,
                                'fast_name': fast.name,
                                'church_name': fast.church.name if fast.church else None,
                                'completion_date': yesterday.isoformat(),
                            }
                        )
                        if milestone:
                            milestones_awarded += 1
                            logger.info(f"Awarded first non-weekly fast completion milestone to {user.username} for {fast.name}")
                        
                except Exception as e:
                    logger.error(f"Error processing user {profile.user.username} for fast {fast.name}: {e}")
                    continue
        
        logger.info(f"Processed {users_processed} users, awarded {milestones_awarded} completion milestones")
        return {
            'users_processed': users_processed,
            'milestones_awarded': milestones_awarded,
            'completed_fasts_checked': completed_fasts.count()
        }
        
    except Exception as e:
        logger.error(f"Error checking completed fast milestones: {e}")
        return {
            'users_processed': 0,
            'milestones_awarded': 0,
            'completed_fasts_checked': 0,
            'error': str(e)
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def create_announcement_feed_items_task(self, announcement_id):
    """
    Create activity feed items for all target users when an announcement is published.
    
    Args:
        announcement_id: ID of the Announcement
    """
    try:
        from .models import Announcement, UserActivityFeed
        
        announcement = Announcement.objects.get(id=announcement_id)
        
        # Get target users
        target_users = announcement.get_target_users()
        
        created_count = 0
        for user in target_users:
            try:
                # Check if user already has this announcement in their feed
                from django.contrib.contenttypes.models import ContentType
                announcement_ct = ContentType.objects.get_for_model(announcement)
                existing_item = UserActivityFeed.objects.filter(
                    user=user,
                    activity_type='announcement',
                    content_type=announcement_ct,
                    object_id=announcement.id
                ).exists()
                
                if not existing_item:
                    UserActivityFeed.create_announcement_item(user, announcement)
                    created_count += 1
                    
            except Exception as e:
                logger.error(f"Error creating announcement feed item for user {user.username}: {e}")
                continue
        
        # Update total recipients count
        announcement.total_recipients = created_count
        announcement.save(update_fields=['total_recipients'])
        
        logger.info(f"Created {created_count} announcement feed items for announcement '{announcement.title}'")
        
        return {
            'announcement_id': announcement_id,
            'announcement_title': announcement.title,
            'recipients_count': created_count,
            'target_all_users': announcement.target_all_users
        }
        
    except Announcement.DoesNotExist:
        logger.error(f"Announcement {announcement_id} not found")
        return {
            'announcement_id': announcement_id,
            'recipients_count': 0,
            'error': 'Announcement not found'
        }
    except Exception as exc:
        logger.error(f"Error creating announcement feed items for announcement {announcement_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc) 