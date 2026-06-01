"""Signal handlers for icon view cache invalidation and deduplication."""

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from taggit.models import TaggedItem

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


@receiver(post_save, sender=TaggedItem)
@receiver(post_delete, sender=TaggedItem)
def invalidate_icon_tag_cache(sender, instance, **kwargs):
    """Clear icon API response caches when icon tag assignments change."""
    icon_content_type = ContentType.objects.get_for_model(Icon)
    if instance.content_type_id == icon_content_type.id:
        IconViewCache.clear_all()
