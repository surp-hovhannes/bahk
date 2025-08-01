"""
Celery tasks for learning resources operations.
"""
import logging
from typing import Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from celery import shared_task
from celery.utils.log import get_task_logger
from .models import Bookmark
from .cache import BookmarkCacheManager

# Set up logging for Celery tasks
logger = get_task_logger(__name__)


@shared_task(bind=True, max_retries=2)
def cleanup_orphaned_bookmarks_async(
    self, 
    content_type_filter: Optional[str] = None,
    batch_size: int = 1000,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Asynchronously clean up orphaned bookmarks (bookmarks pointing to non-existent content objects).
    
    This task processes bookmarks in batches to handle large datasets efficiently
    without blocking the main application.
    
    Args:
        content_type_filter: Optional content type to filter by (e.g., "video", "article")
        batch_size: Number of bookmarks to process per batch (default: 1000)
        dry_run: If True, only report what would be deleted without actually deleting
        
    Returns:
        Dict with task results including counts and timing
    """
    task_id = self.request.id
    start_time = timezone.now()
    
    logger.info(f"Starting orphaned bookmark cleanup task {task_id}")
    logger.info(f"Parameters: content_type_filter={content_type_filter}, batch_size={batch_size}, dry_run={dry_run}")
    
    try:
        # Build queryset
        bookmarks_qs = Bookmark.objects.select_related('content_type', 'user')
        
        if content_type_filter:
            try:
                content_type = ContentType.objects.get(model=content_type_filter.lower())
                bookmarks_qs = bookmarks_qs.filter(content_type=content_type)
                logger.info(f"Filtering by content type: {content_type_filter}")
            except ContentType.DoesNotExist:
                error_msg = f"Invalid content type filter: {content_type_filter}"
                logger.error(error_msg)
                return {
                    "status": "error",
                    "error": error_msg,
                    "task_id": task_id,
                    "duration_seconds": 0
                }
        
        total_bookmarks = bookmarks_qs.count()
        logger.info(f"Processing {total_bookmarks} bookmarks in batches of {batch_size}")
        
        # Process in batches
        processed = 0
        orphaned_count = 0
        deleted_count = 0
        orphaned_by_type = {}
        
        # Update task progress
        self.update_state(
            state='PROGRESS',
            meta={
                'current': 0,
                'total': total_bookmarks,
                'status': 'Processing bookmarks...'
            }
        )
        
        batch_start = 0
        while batch_start < total_bookmarks:
            batch_end = min(batch_start + batch_size, total_bookmarks)
            
            # Get batch of bookmarks with their IDs to handle potential deletions
            batch_bookmarks = list(
                bookmarks_qs[batch_start:batch_end].values(
                    'id', 'user_id', 'content_type_id', 'object_id', 'content_type__model'
                )
            )
            
            batch_orphaned = []
            
            # Check each bookmark in the batch
            for bookmark_data in batch_bookmarks:
                processed += 1
                
                # Get the actual bookmark object to check content_object
                try:
                    bookmark = Bookmark.objects.select_related('content_type').get(
                        id=bookmark_data['id']
                    )
                    
                    # Check if content object exists
                    if bookmark.content_object is None:
                        orphaned_count += 1
                        batch_orphaned.append(bookmark)
                        
                        # Track by content type
                        content_type_name = bookmark_data['content_type__model']
                        orphaned_by_type[content_type_name] = orphaned_by_type.get(content_type_name, 0) + 1
                        
                        logger.debug(f"Found orphaned bookmark: ID={bookmark.id}, Type={content_type_name}, Object={bookmark_data['object_id']}")
                        
                except Bookmark.DoesNotExist:
                    # Bookmark was deleted while we were processing (rare edge case)
                    logger.warning(f"Bookmark ID {bookmark_data['id']} disappeared during processing")
                    continue
            
            # Delete orphaned bookmarks in this batch
            if batch_orphaned and not dry_run:
                with transaction.atomic():
                    for bookmark in batch_orphaned:
                        # Update cache before deletion
                        BookmarkCacheManager.bookmark_deleted(
                            user=bookmark.user,
                            content_type=bookmark.content_type,
                            object_id=bookmark.object_id
                        )
                        
                        bookmark.delete()
                        deleted_count += 1
            
            # Update progress
            progress_percent = int((processed / total_bookmarks) * 100)
            self.update_state(
                state='PROGRESS',
                meta={
                    'current': processed,
                    'total': total_bookmarks,
                    'progress_percent': progress_percent,
                    'orphaned_found': orphaned_count,
                    'deleted': deleted_count,
                    'status': f'Processed {processed}/{total_bookmarks} bookmarks'
                }
            )
            
            logger.info(f"Batch progress: {processed}/{total_bookmarks} ({progress_percent}%) - Found {len(batch_orphaned)} orphaned in this batch")
            
            batch_start += batch_size
        
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds()
        
        result = {
            "status": "completed",
            "task_id": task_id,
            "total_processed": processed,
            "orphaned_found": orphaned_count,
            "deleted": deleted_count if not dry_run else 0,
            "orphaned_by_type": orphaned_by_type,
            "dry_run": dry_run,
            "duration_seconds": duration,
            "batch_size": batch_size,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }
        
        if dry_run:
            logger.info(f"DRY RUN: Would delete {orphaned_count} orphaned bookmarks")
        else:
            logger.info(f"Successfully deleted {deleted_count} orphaned bookmarks")
        
        logger.info(f"Task {task_id} completed in {duration:.2f} seconds")
        
        return result
        
    except Exception as exc:
        error_msg = f"Task failed with error: {str(exc)}"
        logger.error(error_msg, exc_info=True)
        
        # Update task state to failure
        self.update_state(
            state='FAILURE',
            meta={
                'error': error_msg,
                'task_id': task_id
            }
        )
        
        # Re-raise the exception for Celery to handle
        raise self.retry(exc=exc, countdown=60, max_retries=2)


@shared_task(bind=True)
def bulk_cleanup_bookmarks_async(self, content_type_id: int, object_id: int, force_sync: bool = False) -> Dict[str, Any]:
    """
    Asynchronously clean up bookmarks for a deleted content object.
    
    This task is used when an object with many bookmarks is deleted to avoid
    blocking the main deletion operation.
    
    Args:
        content_type_id: ID of the ContentType for the deleted object
        object_id: ID of the deleted object
        force_sync: If True, force synchronous processing even for large datasets
        
    Returns:
        Dict with cleanup results
    """
    task_id = self.request.id
    start_time = timezone.now()
    
    logger.info(f"Starting bulk bookmark cleanup task {task_id} for ContentType {content_type_id}, Object {object_id}")
    
    try:
        from .models import Bookmark
        from .cache import BookmarkCacheManager
        
        # Get content type
        try:
            content_type = ContentType.objects.get(id=content_type_id)
        except ContentType.DoesNotExist:
            error_msg = f"ContentType {content_type_id} not found"
            logger.error(error_msg)
            return {
                "status": "error",
                "error": error_msg,
                "task_id": task_id
            }
        
        # Find all bookmarks for this object
        orphaned_bookmarks = Bookmark.objects.filter(
            content_type=content_type,
            object_id=object_id
        ).select_related('user', 'content_type')
        
        count = orphaned_bookmarks.count()
        
        if count == 0:
            logger.info(f"No bookmarks found for {content_type.model} ID {object_id}")
            return {
                "status": "completed",
                "task_id": task_id,
                "bookmarks_deleted": 0,
                "duration_seconds": (timezone.now() - start_time).total_seconds()
            }
        
        logger.info(f"Found {count} bookmarks to clean up for {content_type.model} ID {object_id}")
        
        # Prepare data for bulk cache update
        bookmarks_data = []
        for bookmark in orphaned_bookmarks:
            bookmarks_data.append({
                'user_id': bookmark.user_id,
                'content_type_id': bookmark.content_type_id,
                'object_id': bookmark.object_id
            })
        
        # Update cache in bulk before deleting bookmarks
        BookmarkCacheManager.bulk_bookmark_deleted(bookmarks_data)
        
        # Delete bookmarks in bulk
        deleted_count = orphaned_bookmarks.delete()[0]
        
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds()
        
        result = {
            "status": "completed",
            "task_id": task_id,
            "content_type": content_type.model,
            "object_id": object_id,
            "bookmarks_deleted": deleted_count,
            "duration_seconds": duration,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }
        
        logger.info(f"Bulk cleanup completed: deleted {deleted_count} bookmarks for {content_type.model} ID {object_id} in {duration:.2f}s")
        
        return result
        
    except Exception as exc:
        error_msg = f"Bulk cleanup task failed: {str(exc)}"
        logger.error(error_msg, exc_info=True)
        
        self.update_state(
            state='FAILURE',
            meta={
                'error': error_msg,
                'task_id': task_id
            }
        )
        
        raise


@shared_task
def bookmark_cache_maintenance():
    """
    Periodic task to perform bookmark cache maintenance.
    
    This task can be scheduled to run periodically to:
    - Clear stale cache entries
    - Warm up frequently accessed caches
    - Generate cache statistics
    """
    logger.info("Starting bookmark cache maintenance")
    
    try:
        # This could be expanded to include cache warming, stats collection, etc.
        # For now, we'll just log that maintenance ran
        
        result = {
            "status": "completed",
            "maintenance_type": "basic",
            "timestamp": timezone.now().isoformat()
        }
        
        logger.info("Bookmark cache maintenance completed successfully")
        return result
        
    except Exception as exc:
        logger.error(f"Cache maintenance failed: {str(exc)}", exc_info=True)
        raise