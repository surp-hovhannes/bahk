"""Signal handlers for icon view cache invalidation and deduplication."""

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from taggit.models import Tag, TaggedItem

from icons.cache import IconViewCache
from icons.models import DuplicateIconError, Icon
from icons.utils import find_exact_image_hash, find_similar_phash


@receiver(pre_save, sender=Icon)
def detect_duplicate_icon(sender, instance, **kwargs):
    """Reject new icons that duplicate an existing exact or perceptual hash."""
    if not instance._state.adding:
        return
    if not instance.image_hash and not instance.phash:
        return

    exact_match = find_exact_image_hash(instance.image_hash)
    if exact_match:
        raise DuplicateIconError(existing_icon=exact_match)

    similar_match = find_similar_phash(instance.phash, threshold=3)
    if similar_match:
        raise DuplicateIconError(existing_icon=similar_match)


@receiver(post_save, sender=Icon)
@receiver(post_delete, sender=Icon)
def invalidate_icon_view_cache(sender, **kwargs):
    """Clear icon API response caches when icon records change."""
    IconViewCache.clear_all()
    from django.db import transaction
    transaction.on_commit(IconViewCache.clear_all)


@receiver(post_save, sender=TaggedItem)
@receiver(post_delete, sender=TaggedItem)
def invalidate_icon_tag_cache(sender, instance, **kwargs):
    """Clear icon API response caches when icon tag assignments change."""
    icon_content_type = ContentType.objects.get_for_model(Icon)
    if instance.content_type_id == icon_content_type.id:
        IconViewCache.clear_all()


@receiver(m2m_changed, sender=Icon.tags.through)
def taxonomy_tags_changed(sender, instance, action, reverse, pk_set, **kwargs):
    from icons.services.ingestion import schedule
    if reverse:
        if action == 'pre_clear':
            instance._taxonomy_clear_ids = list(TaggedItem.objects.filter(
                tag_id=instance.pk, content_type=ContentType.objects.get_for_model(Icon)
            ).values_list('object_id', flat=True))
        if action in {'post_add', 'post_remove', 'post_clear'}:
            for pk in (getattr(instance, '_taxonomy_clear_ids', []) if action == 'post_clear' else (pk_set or [])):
                schedule(pk)
    elif isinstance(instance, Icon) and action in {'post_add', 'post_remove', 'post_clear'}:
        schedule(instance.pk)


@receiver(post_delete, sender=TaggedItem)
@receiver(post_save, sender=TaggedItem)
def taxonomy_tag_row_changed(sender, instance, **kwargs):
    from django.db import transaction
    from icons.services.ingestion import schedule
    origin = kwargs.get('origin')
    origin_model = getattr(origin, 'model', type(origin))
    if kwargs.get('signal') is post_delete and origin_model not in {Tag, TaggedItem}:
        # A parent deletion collector may already have deleted the outbox while
        # the Icon row still exists. Never recreate a child during that cascade.
        # Direct tag/through-row operations remain transactionally scheduled.
        transaction.on_commit(lambda icon_id=instance.object_id: schedule(icon_id))
        return
    if instance.content_type_id == ContentType.objects.get_for_model(Icon).pk:
        schedule(instance.object_id)


@receiver(pre_save, sender=Tag)
def taxonomy_tag_rename_before(sender, instance, **kwargs):
    if instance.pk:
        instance._taxonomy_icon_ids = list(TaggedItem.objects.filter(
            tag_id=instance.pk, content_type=ContentType.objects.get_for_model(Icon)
        ).values_list('object_id', flat=True))


@receiver(post_save, sender=Tag)
def taxonomy_tag_rename_after(sender, instance, **kwargs):
    from django.db import transaction
    from icons.services.ingestion import schedule
    for pk in getattr(instance, '_taxonomy_icon_ids', []):
        schedule(pk)
    transaction.on_commit(IconViewCache.clear_all)
