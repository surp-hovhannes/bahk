import logging

from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.db import transaction
from django.dispatch import receiver
from django.core.cache import cache
from hub.cache import (
    invalidate_feast_api_cache_for_church,
    invalidate_feast_api_cache_for_feast,
)
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


@receiver(m2m_changed, sender=Profile.fasts.through)
def track_fast_participation(sender, instance, action, reverse, pk_set, using, **kwargs):
    """Serialize mutations per profile, and stamp only real membership changes.

    m2m managers wrap pre/post signals and the through mutation in one atomic
    block. Fast locks protect reverse clears; profile locks exist even when
    the audit row does not, protecting
    concurrent joins/removes/clear on either side of the relation.
    """
    from django.utils import timezone
    from hub.models import Fast, FastParticipation

    if action not in {'pre_add', 'pre_remove', 'pre_clear', 'post_add', 'post_remove', 'post_clear'}:
        return
    through = sender.objects.using(using)
    if action.startswith('pre_'):
        # Lock fasts first in a stable order as well: reverse clear must exclude
        # concurrent additions of profiles not yet in its initial member set.
        fast_ids = [instance.pk] if reverse else (
            sorted(through.filter(profile_id=instance.pk).values_list("fast_id", flat=True))
            if action == "pre_clear" else sorted(pk_set or [])
        )
        list(Fast.objects.using(using).select_for_update().filter(pk__in=fast_ids).order_by("pk"))
        profile_ids = sorted(pk_set or []) if reverse and action != 'pre_clear' else (
            sorted(through.filter(fast_id=instance.pk).values_list('profile_id', flat=True))
            if reverse else [instance.pk]
        )
        list(Profile.objects.using(using).select_for_update().filter(pk__in=profile_ids).order_by('pk'))
        if action in {'pre_remove', 'pre_clear'}:
            memberships = through.filter(fast_id=instance.pk) if reverse else through.filter(profile_id=instance.pk)
            if action == 'pre_remove':
                memberships = memberships.filter(profile_id__in=pk_set) if reverse else memberships.filter(fast_id__in=pk_set)
            instance._participation_removed_pairs = list(memberships.values_list('profile_id', 'fast_id'))
        return

    now = timezone.now()
    # Reset for every post signal, including empty/no-op mutations; the event
    # receiver consumes this metadata immediately on the same instance.
    instance._fast_participation_changes = {}
    if action == 'post_add':
        pairs = [(pk, instance.pk) if reverse else (instance.pk, pk) for pk in sorted(pk_set or [])]
        fast_ids = {fast_id for _, fast_id in pairs}
        fasts = {fast.pk: fast for fast in Fast.objects.using(using).with_dates().filter(pk__in=fast_ids)}
        for profile_id, fast_id in pairs:
            fast = fasts[fast_id]
            period, _ = FastParticipation.objects.using(using).get_or_create(
                profile_id=profile_id, fast_id=fast_id, left_at=None, ended_at_unknown=False,
                defaults={'joined_at': now, 'fast_original_id': fast.pk, 'fast_name': fast.name,
                          'fast_year': fast.year, 'fast_end_date': fast.end_date},
            )
            instance._fast_participation_changes[fast_id] = period.pk
    else:
        for profile_id, fast_id in getattr(instance, '_participation_removed_pairs', []):
            instance._fast_participation_changes[fast_id] = _stamp_participation_leave(
                profile_id, fast_id, now, using=using,
            )
        instance.__dict__.pop('_participation_removed_pairs', None)


def _stamp_participation_leave(profile_id, fast_id, now, *, using):
    """Close a known runtime period; orphan leave evidence belongs to backfill."""
    from hub.models import FastParticipation

    with transaction.atomic(using=using):
        periods = FastParticipation.objects.using(using).select_for_update().filter(
            profile_id=profile_id, fast_id=fast_id, left_at__isnull=True, ended_at_unknown=False,
        )
        for period in periods:
            period.left_at = now
            period.save(using=using, update_fields=['left_at'])
            return period.pk
    return None


@receiver(pre_delete, sender='hub.Fast')
def preserve_deleted_fast_participations(sender, instance, using, **kwargs):
    """Snapshot before SET_NULL; freeze open completion at the deletion instant."""
    from django.db.models import Max
    from django.utils import timezone
    from hub.models import FastParticipation

    # Match membership/reconciliation order: Fast before audit rows.
    list(sender.objects.using(using).select_for_update().filter(pk=instance.pk))
    end = instance.days.using(using).filter(church_id=instance.church_id).aggregate(end=Max('date'))['end']
    FastParticipation.objects.using(using).filter(fast_id=instance.pk).update(
        fast_original_id=instance.pk, fast_name=instance.name, fast_year=instance.year,
        fast_end_date=end, fast_deleted_at=timezone.now(),
    )


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
    """Invalidate feast API cache entries only after the context write commits."""
    if getattr(instance, "_feast_cache_invalidation_managed", False):
        return
    church_id = instance.feast.church_id
    transaction.on_commit(lambda: invalidate_feast_api_cache_for_church(church_id))


@receiver(post_delete, sender=FeastContext)
def handle_feast_context_delete(sender, instance, **kwargs):
    """Invalidate feast API cache entries only after the context delete commits."""
    church_id = instance.feast.church_id
    transaction.on_commit(lambda: invalidate_feast_api_cache_for_church(church_id))


def invalidate_feast_api_cache_for_icon(icon):
    """Invalidate feast API cache entries for every feast using an icon."""
    for feast in icon.feasts.only("church_id").all():
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
