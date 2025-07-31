"""
Django signals for automatic bookmark cache management.

This module ensures Redis cache consistency by automatically invalidating
or updating cache when bookmark models change through any method
(API, admin, shell, etc.).
"""

import logging
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from .models import Bookmark
from .cache import BookmarkCacheManager

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Bookmark)
def bookmark_created_signal(sender, instance, created, **kwargs):
    """
    Handle bookmark creation via Django signals.
    
    This ensures cache consistency when bookmarks are created through
    any method (API, admin, Django shell, etc.).
    """
    if created:
        BookmarkCacheManager.bookmark_created(
            user=instance.user,
            content_type=instance.content_type,
            object_id=instance.object_id
        )
        logger.debug(f"Signal: Bookmark created for user {instance.user.id}, "
                    f"content_type {instance.content_type.id}, object {instance.object_id}")


@receiver(pre_delete, sender=Bookmark)
def bookmark_deleted_signal(sender, instance, **kwargs):
    """
    Handle bookmark deletion via Django signals.
    
    We use pre_delete instead of post_delete to ensure we have access
    to the bookmark data before it's removed from the database.
    """
    BookmarkCacheManager.bookmark_deleted(
        user=instance.user,
        content_type=instance.content_type,
        object_id=instance.object_id
    )
    logger.debug(f"Signal: Bookmark deleted for user {instance.user.id}, "
                f"content_type {instance.content_type.id}, object {instance.object_id}")


# Optional: Handle user deletion to clean up their bookmark cache
try:
    from django.contrib.auth.models import User
    
    @receiver(post_delete, sender=User)
    def user_deleted_signal(sender, instance, **kwargs):
        """
        Clean up bookmark cache when a user is deleted.
        
        This prevents memory leaks in Redis by removing cached data
        for deleted users.
        """
        BookmarkCacheManager.user_cache_invalidated(instance)
        logger.debug(f"Signal: User {instance.id} deleted, bookmark cache cleaned up")
        
except ImportError:
    # In case User model is not available
    logger.warning("Could not register User deletion signal for bookmark cache cleanup")