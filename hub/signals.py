import logging

from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.db import transaction
from django.dispatch import receiver
from django.core.cache import cache
from hub.cache import invalidate_feast_api_cache_for_feast
from hub.models import Profile, Feast, FeastContext
from hub.tasks.llm_tasks import determine_feast_designation_task
from hub.tasks.icon_tasks import match_icon_to_feast_task
from icons.models import Icon

logger = logging.getLogger(__name__)

@receiver(m2m_changed, sender=Profile.fasts.through)
def handle_fast_participant_change(sender, instance, action, **kwargs):
    """
    Signal handler that invalidates the FastListView cache when
    participants join or leave fasts.

    This triggers on any change to the many-to-many relationship
    between Profile and Fast models.
    """
    # Only proceed for these specific actions
    if action in ('post_add', 'post_remove', 'post_clear'):
        # Determine the church ID based on the instance type
        church_id = None

        if isinstance(instance, Profile):
            # This is a Profile instance, get its church ID
            church_id = instance.church_id
        else:
            # This is a Fast instance, get its church ID
            church_id = instance.church_id

        if church_id:
            # Invalidate the participant count cache
            cache.delete(f'church_{church_id}_participant_count')

            # Also clear all cached querysets for this church
            pattern = f'fast_list_qs:{church_id}:*'
            # Note: If your cache backend doesn't support pattern matching,
            # this will need a different approach
            keys = cache.keys(pattern) if hasattr(cache, 'keys') else []
            if keys:
                cache.delete_many(keys)


@receiver(post_save, sender=Feast)
def handle_feast_save(sender, instance, created, **kwargs):
    """
    Signal handler that triggers designation determination when a feast is created
    (if designation is not already set).

    Also triggers icon matching when a feast is created.

    Only triggers designation task on creation to avoid duplicate enqueuing when
    translations are updated immediately after creation.
    The task itself will also check and skip if designation is already set.
    """
    transaction.on_commit(lambda: invalidate_feast_api_cache_for_feast(instance))

    # Only trigger designation task on creation, not on updates
    # This prevents duplicate task enqueuing when translations are set immediately after creation
    if created and not instance.designation:
        # Trigger designation determination task
        # The task will handle the actual determination and will skip if designation is already set
        determine_feast_designation_task.delay(instance.id)

    # Trigger icon matching when feast is created
    if created:
        match_icon_to_feast_task.delay(instance.id)


@receiver(post_delete, sender=Feast)
def handle_feast_delete(sender, instance, **kwargs):
    """Invalidate feast API cache entries when a feast is deleted."""
    invalidate_feast_api_cache_for_feast(instance)


@receiver(post_save, sender=FeastContext)
def handle_feast_context_save(sender, instance, **kwargs):
    """Invalidate feast API cache entries when context content or votes change."""
    invalidate_feast_api_cache_for_feast(instance.feast)


@receiver(post_delete, sender=FeastContext)
def handle_feast_context_delete(sender, instance, **kwargs):
    """Invalidate feast API cache entries when context is deleted."""
    invalidate_feast_api_cache_for_feast(instance.feast)


def invalidate_feast_api_cache_for_icon(icon):
    """Invalidate feast API cache entries for every feast using an icon."""
    for feast in icon.feasts.select_related("day", "day__church").all():
        invalidate_feast_api_cache_for_feast(feast)


@receiver(post_save, sender=Icon)
def handle_icon_save(sender, instance, **kwargs):
    """Invalidate feast API cache entries when serialized icon fields change."""
    invalidate_feast_api_cache_for_icon(instance)


@receiver(pre_delete, sender=Icon)
def handle_icon_delete(sender, instance, **kwargs):
    """Invalidate feast API cache entries before icon deletion clears feast links."""
    invalidate_feast_api_cache_for_icon(instance)


@receiver(m2m_changed, sender=Icon.tags.through)
def handle_icon_tags_change(sender, instance, action, **kwargs):
    """Invalidate feast API cache entries when serialized icon tags change."""
    if isinstance(instance, Icon) and action in ("post_add", "post_remove", "post_clear"):
        invalidate_feast_api_cache_for_icon(instance)
