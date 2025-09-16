"""
Django signals for automatically tracking user events.
These signals listen for specific model changes and create corresponding event records.
"""

import logging
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save, post_delete, m2m_changed, pre_save
from django.dispatch import receiver
@receiver(pre_save, sender='hub.Fast')
def cache_fast_original_values(sender, instance, **kwargs):
    """
    Cache original values for selected Fast fields before save, so we can compute diffs in post_save.
    """
    try:
        if not instance.pk:
            return
        from hub.models import Fast
        original = Fast.objects.filter(pk=instance.pk).first()
        try:
            original = Fast.objects.get(pk=instance.pk)
        except Fast.DoesNotExist:
            return
        # Store a lightweight snapshot of original values
        instance._original_values = {
            'name': original.name,
            'description': original.description,
            'church_id': original.church_id,
            'culmination_feast': getattr(original, 'culmination_feast', None),
            'culmination_feast_date': getattr(original, 'culmination_feast_date', None),
            'url': getattr(original, 'url', None),
            'image': (original.image.name if getattr(original, 'image', None) else None),
        }
    except Exception as e:
        logger.error(f"Error caching original Fast values: {e}")

from django.contrib.contenttypes.models import ContentType

from .models import Event, EventType

logger = logging.getLogger(__name__)


@receiver(m2m_changed)
def track_fast_membership_changes(sender, instance, action, pk_set, **kwargs):
    """
    Track when users join or leave fasts.
    This signal is triggered when the Profile.fasts ManyToMany relationship changes.
    """
    from hub.models import Fast, Profile  # Import here to avoid circular imports
    
    # Only process signals from the Profile.fasts relationship
    if sender != Profile.fasts.through or not hasattr(instance, 'user') or action not in ['post_add', 'post_remove'] or not pk_set:
        return
        
    try:
        # Get the user from the profile
        user = instance.user
        
        for fast_pk in pk_set:
            try:
                fast = Fast.objects.get(pk=fast_pk)
                
                if action == 'post_add':
                    # User joined a fast
                    try:
                        # Pull attribution data from profile
                        utm_source = getattr(user.profile, 'utm_source', None)
                        utm_campaign = getattr(user.profile, 'utm_campaign', None)
                        join_source = getattr(user.profile, 'join_source', None)
                        Event.create_event(
                            event_type_code=EventType.USER_JOINED_FAST,
                            user=user,
                            target=fast,
                            description=f"User {user} joined the fast '{fast.name}'",
                            data={
                                'fast_id': fast.id,
                                'fast_name': fast.name,
                                'church_id': fast.church.id if fast.church else None,
                                'church_name': fast.church.name if fast.church else None,
                                'user_id': user.id,
                                'username': user.username,
                                'utm_source': utm_source,
                                'utm_campaign': utm_campaign,
                                'join_source': join_source,
                            }
                        )
                        logger.info(f"Tracked USER_JOINED_FAST event: {user} joined {fast}")
                        
                        # Check for user milestone: first fast join
                        try:
                            from .models import UserMilestone
                            
                            # Only award if user doesn't already have this milestone
                            # and this is actually their first fast
                            if not UserMilestone.objects.filter(
                                user=user,
                                milestone_type='first_fast_join'
                            ).exists():
                                # Check if this is truly their first fast
                                user_fast_count = user.profile.fasts.count()
                                
                                if user_fast_count == 1:  # This is their first fast
                                    milestone = UserMilestone.create_milestone(
                                        user=user,
                                        milestone_type='first_fast_join',
                                        related_object=fast,
                                        data={
                                            'fast_id': fast.id,
                                            'fast_name': fast.name,
                                            'church_name': fast.church.name if fast.church else None,
                                        }
                                    )
                                    if milestone:
                                        logger.info(f"User {user.username} achieved first fast join milestone with {fast.name}")
                        except Exception as milestone_error:
                            logger.error(f"Error creating first fast join milestone for {user.username}: {milestone_error}")
                        
                        # Check for participation milestones after user joins
                        try:
                            from .tasks import track_fast_participant_milestone_task
                            track_fast_participant_milestone_task.delay(fast.id)
                            logger.info(f"Scheduled milestone check for fast {fast.name} after user join")
                        except Exception as milestone_error:
                            logger.error(f"Error scheduling milestone check for fast {fast.name}: {milestone_error}")
                            
                    except ValueError as e:
                        if "does not exist or is inactive" in str(e):
                            logger.warning(f"Event type '{EventType.USER_JOINED_FAST}' does not exist, skipping event tracking")
                        else:
                            raise
                        
                elif action == 'post_remove':
                    # User left a fast
                    try:
                        Event.create_event(
                            event_type_code=EventType.USER_LEFT_FAST,
                            user=user,
                            target=fast,
                            description=f"User {user} left the fast '{fast.name}'",
                            data={
                                'fast_id': fast.id,
                                'fast_name': fast.name,
                                'church_id': fast.church.id if fast.church else None,
                                'church_name': fast.church.name if fast.church else None,
                                'user_id': user.id,
                                'username': user.username,
                            }
                        )
                        logger.info(f"Tracked USER_LEFT_FAST event: {user} left {fast}")
                        
                        # Check for participation milestones after user leaves
                        try:
                            from .tasks import track_fast_participant_milestone_task
                            track_fast_participant_milestone_task.delay(fast.id)
                            logger.info(f"Scheduled milestone check for fast {fast.name} after user leave")
                        except Exception as milestone_error:
                            logger.error(f"Error scheduling milestone check for fast {fast.name}: {milestone_error}")
                            
                    except ValueError as e:
                        if "does not exist or is inactive" in str(e):
                            logger.warning(f"Event type '{EventType.USER_LEFT_FAST}' does not exist, skipping event tracking")
                        else:
                            raise
                        
            except Fast.DoesNotExist:
                logger.warning(f"Fast with ID {fast_pk} not found while tracking membership change")
            except Exception as e:
                logger.error(f"Error tracking fast membership change: {e}")
                
    except Exception as e:
        logger.error(f"Error in track_fast_membership_changes signal: {e}")


@receiver(post_save, sender='hub.Fast')
def track_fast_creation_and_updates(sender, instance, created, **kwargs):
    """
    Track when fasts are created or updated.
    """
    try:
        # Check if event types exist before trying to create events
        if created:
            # Fast was created
            try:
                Event.create_event(
                    event_type_code=EventType.FAST_CREATED,
                    user=None,  # System event
                    target=instance,
                    description=f"Fast '{instance.name}' was created",
                    data={
                        'fast_id': instance.id,
                        'fast_name': instance.name,
                        'church_id': instance.church.id if instance.church else None,
                        'church_name': instance.church.name if instance.church else None,
                        'year': instance.year,
                    }
                )
                logger.info(f"Tracked FAST_CREATED event: {instance}")
            except ValueError as e:
                if "does not exist or is inactive" in str(e):
                    logger.warning(f"Event type '{EventType.FAST_CREATED}' does not exist, skipping event tracking")
                else:
                    raise
        else:
            # Fast was updated
            # Note: We could add more sophisticated tracking here to see what fields changed
            try:
                # Determine changed fields using pre_save snapshot
                update_fields = kwargs.get('update_fields')
                original_values = getattr(instance, '_original_values', {})
                meaningful_fields = {
                    'name', 'description', 'church_id',
                    'culmination_feast', 'culmination_feast_date', 'url', 'image'
                }
                system_only_fields = {'year', 'cached_thumbnail_url', 'cached_thumbnail_updated'}

                # Build current values for comparison
                current_values = {
                    'name': instance.name,
                    'description': instance.description,
                    'church_id': instance.church_id,
                    'culmination_feast': getattr(instance, 'culmination_feast', None),
                    'culmination_feast_date': getattr(instance, 'culmination_feast_date', None),
                    'url': getattr(instance, 'url', None),
                    'image': (instance.image.name if getattr(instance, 'image', None) else None),
                }

                # Select which fields to check
                fields_to_check = set(current_values.keys())
                # Only filter by update_fields if it is explicitly provided (not None)
                if update_fields is not None:
                    fields_to_check &= set(update_fields)

                # Compute diffs
                diffs = {}
                for field in fields_to_check:
                    old_value = original_values.get(field)
                    new_value = current_values.get(field)
                    if old_value != new_value:
                        diffs[field] = {'old': old_value, 'new': new_value}

                # If only system-maintained fields changed (or nothing changed), skip
                if not diffs:
                    return
                if set(diffs.keys()).issubset(system_only_fields):
                    return

                # Keep only meaningful diffs for payload and decision to emit
                meaningful_diffs = {k: v for k, v in diffs.items() if k in meaningful_fields}
                if not meaningful_diffs:
                    return

                Event.create_event(
                    event_type_code=EventType.FAST_UPDATED,
                    user=None,  # System event (could be enhanced to track who made the change)
                    target=instance,
                    description=f"Fast '{instance.name}' was updated",
                    data={
                        'fast_id': instance.id,
                        'fast_name': instance.name,
                        'church_id': instance.church.id if instance.church else None,
                        'church_name': instance.church.name if instance.church else None,
                        'year': instance.year,
                        'updated_fields': sorted(list(meaningful_diffs.keys())),
                        'field_diffs': meaningful_diffs,
                    }
                )
                logger.info(f"Tracked FAST_UPDATED event: {instance}")
            except ValueError as e:
                if "does not exist or is inactive" in str(e):
                    logger.warning(f"Event type '{EventType.FAST_UPDATED}' does not exist, skipping event tracking")
                else:
                    raise
            
    except Exception as e:
        logger.error(f"Error tracking fast creation/update: {e}")


@receiver(user_logged_in)
def track_user_login(sender, request, user, **kwargs):
    """
    Track when users log in.
    """
    try:
        Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=user,
            target=None,
            description=f"User {user} logged in",
            data={
                'user_id': user.id,
                'username': user.username,
                'email': user.email,
            },
            request=request
        )
        logger.info(f"Tracked USER_LOGGED_IN event: {user}")
        
    except ValueError as e:
        if "does not exist or is inactive" in str(e):
            logger.warning(f"Event type '{EventType.USER_LOGGED_IN}' does not exist, skipping event tracking")
        else:
            raise
    except Exception as e:
        logger.error(f"Error tracking user login: {e}")


@receiver(user_logged_out)
def track_user_logout(sender, request, user, **kwargs):
    """
    Track when users log out.
    """
    try:
        if user:  # user might be None if anonymous
            Event.create_event(
                event_type_code=EventType.USER_LOGGED_OUT,
                user=user,
                target=None,
                description=f"User {user} logged out",
                data={
                    'user_id': user.id,
                    'username': user.username,
                    'email': user.email,
                },
                request=request
            )
            logger.info(f"Tracked USER_LOGGED_OUT event: {user}")
            
    except ValueError as e:
        if "does not exist or is inactive" in str(e):
            logger.warning(f"Event type '{EventType.USER_LOGGED_OUT}' does not exist, skipping event tracking")
        else:
            raise
    except Exception as e:
        logger.error(f"Error tracking user logout: {e}")


def track_fast_participant_milestone(fast, participant_count, milestone_type="participant_count"):
    """
    Utility function to track when a fast reaches participation milestones.
    This should be called manually when checking participation levels.
    
    Args:
        fast: Fast instance
        participant_count: Current number of participants
        milestone_type: Type of milestone (e.g., "participant_count", "percentage")
    """
    try:
        # Define milestone thresholds
        milestones = [10, 25, 50, 100, 250, 500, 1000]
        
        # Check if we hit a milestone
        milestone_hit = None
        for milestone in milestones:
            if participant_count == milestone:
                milestone_hit = milestone
                break
        
        if milestone_hit:
            Event.create_event(
                event_type_code=EventType.FAST_PARTICIPANT_MILESTONE,
                user=None,  # System event
                target=fast,
                description=f"Fast '{fast.name}' reached {milestone_hit} participants!",
                data={
                    'fast_id': fast.id,
                    'fast_name': fast.name,
                    'participant_count': participant_count,
                    'milestone': milestone_hit,
                    'milestone_type': milestone_type,
                    'church_id': fast.church.id if fast.church else None,
                    'church_name': fast.church.name if fast.church else None,
                }
            )
            logger.info(f"Tracked FAST_PARTICIPANT_MILESTONE event: {fast} reached {milestone_hit} participants")
            return True
            
    except Exception as e:
        logger.error(f"Error tracking fast participant milestone: {e}")
        
    return False


def track_devotional_available(fast, devotional_info=None):
    """
    Utility function to track when a devotional becomes available for a fast.
    This should be called when devotionals are published or made available.
    
    Args:
        fast: Fast instance
        devotional_info: Dictionary with devotional details
    """
    try:
        data = {
            'fast_id': fast.id,
            'fast_name': fast.name,
            'church_id': fast.church.id if fast.church else None,
            'church_name': fast.church.name if fast.church else None,
        }
        
        # Add devotional info if provided
        if devotional_info:
            data.update(devotional_info)
        
        Event.create_event(
            event_type_code=EventType.DEVOTIONAL_AVAILABLE,
            user=None,  # System event
            target=fast,
            description=f"New devotional available for fast '{fast.name}'",
            data=data
        )
        logger.info(f"Tracked DEVOTIONAL_AVAILABLE event for fast: {fast}")
        
    except Exception as e:
        logger.error(f"Error tracking devotional availability: {e}")


def track_fast_beginning(fast):
    """
    Utility function to track when a fast begins.
    This should be called by a scheduled task or when fast start dates are reached.
    
    Args:
        fast: Fast instance
    """
    try:
        Event.create_event(
            event_type_code=EventType.FAST_BEGINNING,
            user=None,  # System event
            target=fast,
            description=f"Fast '{fast.name}' has begun",
            data={
                'fast_id': fast.id,
                'fast_name': fast.name,
                'church_id': fast.church.id if fast.church else None,
                'church_name': fast.church.name if fast.church else None,
                'year': fast.year,
                'participant_count': fast.profiles.count(),
            }
        )
        logger.info(f"Tracked FAST_BEGINNING event: {fast}")
        
    except Exception as e:
        logger.error(f"Error tracking fast beginning: {e}")


def track_fast_ending(fast):
    """
    Utility function to track when a fast ends.
    This should be called by a scheduled task or when fast end dates are reached.
    
    Args:
        fast: Fast instance
    """
    try:
        Event.create_event(
            event_type_code=EventType.FAST_ENDING,
            user=None,  # System event
            target=fast,
            description=f"Fast '{fast.name}' has ended",
            data={
                'fast_id': fast.id,
                'fast_name': fast.name,
                'church_id': fast.church.id if fast.church else None,
                'church_name': fast.church.name if fast.church else None,
                'year': fast.year,
                'final_participant_count': fast.profiles.count(),
            }
        )
        logger.info(f"Tracked FAST_ENDING event: {fast}")
        
    except Exception as e:
        logger.error(f"Error tracking fast ending: {e}")


def check_and_track_participation_milestones(fast):
    """
    Check current participation for a fast and track any milestones.
    This can be called after someone joins/leaves to check for milestone updates.
    
    Args:
        fast: Fast instance
    """
    try:
        current_count = fast.profiles.count()
        track_fast_participant_milestone(fast, current_count)
        
    except Exception as e:
        logger.error(f"Error checking participation milestones for fast {fast}: {e}")


@receiver(post_save, sender=Event)
def create_activity_feed_item(sender, instance, created, **kwargs):
    """
    Create activity feed items when events are created.
    Supports both synchronous and asynchronous creation.
    """
    if created and instance.user:
        from .models import UserActivityFeed
        from django.conf import settings
        
        # Check if async feed creation is enabled
        use_async = getattr(settings, 'USE_ASYNC_ACTIVITY_FEED', False)
        
        if use_async:
            # Create feed item asynchronously via Celery
            from .tasks import create_activity_feed_item_task
            create_activity_feed_item_task.delay(instance.id, instance.user.id)
        else:
            # Create feed item synchronously (current behavior)
            UserActivityFeed.create_from_event(instance, instance.user)

    # Fan-out select system events to relevant users (participants of the fast)
    if created and not instance.user:
        try:
            code = instance.event_type.code
            # Only propagate certain system events
            if code in (EventType.FAST_BEGINNING, EventType.DEVOTIONAL_AVAILABLE):
                # Ensure the event targets a Fast
                if instance.content_type and instance.content_type.model == 'fast' and instance.object_id:
                    from django.contrib.auth import get_user_model
                    from hub.models import Fast
                    from .tasks import create_activity_feed_item_task

                    fast = Fast.objects.filter(id=instance.object_id).first()
                    if fast:
                        User = get_user_model()
                        user_ids = list(
                            User.objects.filter(profile__fasts=fast)
                            .values_list('id', flat=True)
                            .distinct()
                        )
                        for user_id in user_ids:
                            create_activity_feed_item_task.delay(instance.id, user_id)
        except Exception as e:
            logger.error(f"Error propagating system event {instance.id} to participants: {e}")


@receiver(post_save, sender='hub.Devotional')
def track_devotional_availability_on_save(sender, instance, created, **kwargs):
    """
    Track devotional availability when a devotional is created or updated.
    Only triggers for devotionals with dates today or in the future.
    """
    try:
        from django.utils import timezone
        from .tasks import track_devotional_availability_task
        
        today = timezone.now().date()
        
        # Only track if the devotional's date is today or in the future
        if instance.day and instance.day.date >= today:
            # Schedule the task asynchronously to avoid blocking the save operation
            track_devotional_availability_task.delay(instance.day.fast.id, instance.id)
            logger.info(f"Scheduled devotional availability tracking for {instance.day.fast.name} - {instance.video.title if instance.video else 'Devotional'}")
            
    except Exception as e:
        logger.error(f"Error scheduling devotional availability tracking: {e}")


def track_article_published(article):
    """
    Utility function to track when an article is published.
    
    Args:
        article: Article instance
    """
    try:
        Event.create_event(
            event_type_code=EventType.ARTICLE_PUBLISHED,
            user=None,  # System event
            target=article,
            description=f"Article '{article.title}' was published",
            data={
                'article_id': article.id,
                'article_title': article.title,
                'article_image_url': article.cached_thumbnail_url or (article.thumbnail.url if article.thumbnail else None),
                'published_at': article.created_at.isoformat(),
            }
        )
        logger.info(f"Tracked ARTICLE_PUBLISHED event: {article}")
        
    except Exception as e:
        logger.error(f"Error tracking article publication: {e}")


def track_recipe_published(recipe):
    """
    Utility function to track when a recipe is published.
    
    Args:
        recipe: Recipe instance
    """
    try:
        Event.create_event(
            event_type_code=EventType.RECIPE_PUBLISHED,
            user=None,  # System event
            target=recipe,
            description=f"Recipe '{recipe.title}' was published",
            data={
                'recipe_id': recipe.id,
                'recipe_title': recipe.title,
                'recipe_description': recipe.description,
                'recipe_image_url': recipe.cached_thumbnail_url or (recipe.thumbnail.url if recipe.thumbnail else None),
                'time_required': recipe.time_required,
                'serves': recipe.serves,
                'published_at': recipe.created_at.isoformat(),
            }
        )
        logger.info(f"Tracked RECIPE_PUBLISHED event: {recipe}")
        
    except Exception as e:
        logger.error(f"Error tracking recipe publication: {e}")


def track_video_published(video):
    """
    Utility function to track when a video is published.
    Only tracks general and tutorial videos (not devotionals).
    
    Args:
        video: Video instance
    """
    try:
        # Only track general and tutorial videos
        if video.category not in ['general', 'tutorial']:
            logger.debug(f"Skipping video publication tracking for {video.title} (category: {video.category})")
            return
        
        Event.create_event(
            event_type_code=EventType.VIDEO_PUBLISHED,
            user=None,  # System event
            target=video,
            description=f"Video '{video.title}' was published",
            data={
                'video_id': video.id,
                'video_title': video.title,
                'video_description': video.description,
                'video_category': video.category,
                'video_thumbnail_url': video.cached_thumbnail_url or (video.thumbnail.url if video.thumbnail else None),
                'published_at': video.created_at.isoformat(),
            }
        )
        logger.info(f"Tracked VIDEO_PUBLISHED event: {video}")
        
    except Exception as e:
        logger.error(f"Error tracking video publication: {e}")


@receiver(post_save, sender='learning_resources.Article')
def track_article_publication_on_save(sender, instance, created, **kwargs):
    """
    Track article publication when an article is created.
    """
    try:
        if created:
            # Schedule the task asynchronously to avoid blocking the save operation
            from .tasks import track_article_published_task
            track_article_published_task.delay(instance.id)
            logger.info(f"Scheduled article publication tracking for {instance.title}")
            
    except Exception as e:
        logger.error(f"Error scheduling article publication tracking: {e}")


@receiver(post_save, sender='learning_resources.Recipe')
def track_recipe_publication_on_save(sender, instance, created, **kwargs):
    """
    Track recipe publication when a recipe is created.
    """
    try:
        if created:
            # Schedule the task asynchronously to avoid blocking the save operation
            from .tasks import track_recipe_published_task
            track_recipe_published_task.delay(instance.id)
            logger.info(f"Scheduled recipe publication tracking for {instance.title}")
            
    except Exception as e:
        logger.error(f"Error scheduling recipe publication tracking: {e}")


@receiver(post_save, sender='learning_resources.Video')
def track_video_publication_on_save(sender, instance, created, **kwargs):
    """
    Track video publication when a video is created.
    Only tracks general and tutorial videos.
    """
    try:
        if created and instance.category in ['general', 'tutorial']:
            # Schedule the task asynchronously to avoid blocking the save operation
            from .tasks import track_video_published_task
            track_video_published_task.delay(instance.id)
            logger.info(f"Scheduled video publication tracking for {instance.title}")
        elif created:
            logger.debug(f"Skipping video publication tracking for {instance.title} (category: {instance.category})")
            
    except Exception as e:
        logger.error(f"Error scheduling video publication tracking: {e}")



@receiver(post_save, sender='hub.Profile')
def track_user_account_creation(sender, instance, created, **kwargs):
    """
    Track when user profiles are created (indicating full account setup).
    This tracks the completion of account creation after profile setup.
    """
    from django.conf import settings

    # Allow enabling/disabling this tracking via settings to avoid
    # unintended events in environments like tests/fixtures.
    if not getattr(settings, 'TRACK_USER_ACCOUNT_CREATED', False):
        return

    if not created:
        return  # Only track new profile creation, not updates
        
    try:
        Event.create_event(
            event_type_code=EventType.USER_ACCOUNT_CREATED,
            user=instance.user,
            target=instance,  # The profile is the target of the account creation event
            description=f"User {instance.user.username} completed account creation",
            data={
                'user_id': instance.user.id,
                'username': instance.user.username,
                'email': instance.user.email,
                'profile_id': instance.id,
                'church_id': instance.church.id if instance.church else None,
                'church_name': instance.church.name if instance.church else None,
                'name': instance.name,
            }
        )
        logger.info(f"Tracked USER_ACCOUNT_CREATED event: {instance.user.username}")
        
    except ValueError as e:
        if "does not exist or is inactive" in str(e):
            logger.warning(f"Event type '{EventType.USER_ACCOUNT_CREATED}' does not exist, skipping event tracking")
        else:
            raise
    except Exception as e:
        logger.error(f"Error tracking user account creation: {e}")