from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
from django.core.cache import cache
from hub.models import Profile, Feast
from hub.tasks.llm_tasks import determine_feast_designation_task
from hub.tasks.icon_tasks import match_icon_to_feast_task

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
    # Only trigger designation task on creation, not on updates
    # This prevents duplicate task enqueuing when translations are set immediately after creation
    if created and not instance.designation:
        # Trigger designation determination task
        # The task will handle the actual determination and will skip if designation is already set
        determine_feast_designation_task.delay(instance.id)

    # Trigger icon matching when feast is created
    if created:
        match_icon_to_feast_task.delay(instance.id)
