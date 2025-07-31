"""
Django signals for automatic bookmark cache management and data integrity.

This module ensures:
1. Redis cache consistency by automatically invalidating or updating cache
2. Data integrity by cleaning up orphaned bookmarks when objects are deleted
"""

import logging
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from django.contrib.contenttypes.models import ContentType
from .models import Bookmark, Video, Article, Recipe
from .cache import BookmarkCacheManager

# Import from hub app for DevotionalSet
try:
    from hub.models import DevotionalSet, Devotional, Fast, Reading
except ImportError:
    DevotionalSet = None
    Devotional = None
    Fast = None
    Reading = None

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


# Content object deletion signals to prevent orphaned bookmarks
def cleanup_orphaned_bookmarks(sender, instance, **kwargs):
    """
    Generic function to clean up bookmarks when content objects are deleted.
    
    This ensures data integrity by removing bookmarks that would otherwise
    become orphaned when their referenced objects are deleted.
    """
    content_type = ContentType.objects.get_for_model(sender)
    
    # Find and delete all bookmarks pointing to this object
    orphaned_bookmarks = Bookmark.objects.filter(
        content_type=content_type,
        object_id=instance.id
    )
    
    count = orphaned_bookmarks.count()
    if count > 0:
        # Update cache for affected users before deleting bookmarks
        for bookmark in orphaned_bookmarks:
            BookmarkCacheManager.bookmark_deleted(
                user=bookmark.user,
                content_type=content_type,
                object_id=instance.id
            )
        
        # Delete orphaned bookmarks
        orphaned_bookmarks.delete()
        logger.info(f"Deleted {count} orphaned bookmarks for {sender.__name__} ID {instance.id}")


# Register signals for all bookmarkable content types
@receiver(post_delete, sender=Video)
def video_deleted_signal(sender, instance, **kwargs):
    """Clean up bookmarks when a video is deleted."""
    cleanup_orphaned_bookmarks(sender, instance, **kwargs)


@receiver(post_delete, sender=Article)
def article_deleted_signal(sender, instance, **kwargs):
    """Clean up bookmarks when an article is deleted."""
    cleanup_orphaned_bookmarks(sender, instance, **kwargs)


@receiver(post_delete, sender=Recipe)
def recipe_deleted_signal(sender, instance, **kwargs):
    """Clean up bookmarks when a recipe is deleted."""
    cleanup_orphaned_bookmarks(sender, instance, **kwargs)


# Hub app content types (if available)
if DevotionalSet:
    @receiver(post_delete, sender=DevotionalSet)
    def devotional_set_deleted_signal(sender, instance, **kwargs):
        """Clean up bookmarks when a devotional set is deleted."""
        cleanup_orphaned_bookmarks(sender, instance, **kwargs)

if Devotional:
    @receiver(post_delete, sender=Devotional)
    def devotional_deleted_signal(sender, instance, **kwargs):
        """Clean up bookmarks when a devotional is deleted."""
        cleanup_orphaned_bookmarks(sender, instance, **kwargs)

if Fast:
    @receiver(post_delete, sender=Fast)
    def fast_deleted_signal(sender, instance, **kwargs):
        """Clean up bookmarks when a fast is deleted."""
        cleanup_orphaned_bookmarks(sender, instance, **kwargs)

if Reading:
    @receiver(post_delete, sender=Reading)
    def reading_deleted_signal(sender, instance, **kwargs):
        """Clean up bookmarks when a reading is deleted."""
        cleanup_orphaned_bookmarks(sender, instance, **kwargs)