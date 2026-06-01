"""Signal handlers for icon view cache invalidation."""

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from taggit.models import TaggedItem

from icons.cache import IconViewCache
from icons.models import Icon


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
