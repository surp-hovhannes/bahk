"""
Django signals for automatically tracking user events.
These signals listen for specific model changes and create corresponding event records.
"""

import logging
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
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
                            }
                        )
                        logger.info(f"Tracked USER_JOINED_FAST event: {user} joined {fast}")
                        
                    elif action == 'post_remove':
                        # User left a fast
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
        if created:
            # Fast was created
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
        else:
            # Fast was updated
            # Note: We could add more sophisticated tracking here to see what fields changed
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
                }
            )
            logger.info(f"Tracked FAST_UPDATED event: {instance}")
            
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