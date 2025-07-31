"""
Django management command to clean up orphaned bookmarks.

This command finds and removes bookmarks that point to non-existent objects,
which can happen if objects were deleted before the signal handlers were in place.

Usage:
    python manage.py cleanup_orphaned_bookmarks           # Clean up all orphaned bookmarks (sync)
    python manage.py cleanup_orphaned_bookmarks --dry-run # Show what would be deleted
    python manage.py cleanup_orphaned_bookmarks --stats   # Show statistics only
    python manage.py cleanup_orphaned_bookmarks --async   # Run async cleanup (recommended for large datasets)
    python manage.py cleanup_orphaned_bookmarks --async --wait # Run async and wait for completion
"""

from django.core.management.base import BaseCommand
from django.contrib.contenttypes.models import ContentType
from learning_resources.models import Bookmark
from learning_resources.cache import BookmarkCacheManager
import time


class Command(BaseCommand):
    help = 'Clean up orphaned bookmarks that point to non-existent objects. Use --async for large datasets.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting anything'
        )
        
        parser.add_argument(
            '--stats',
            action='store_true',
            help='Show statistics about orphaned bookmarks without deleting'
        )
        
        parser.add_argument(
            '--content-type',
            type=str,
            help='Only check specific content type (e.g., "video", "article")'
        )
        
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run the cleanup asynchronously using Celery (recommended for large datasets)'
        )
        
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of bookmarks to process per batch in async mode (default: 1000)'
        )
        
        parser.add_argument(
            '--wait',
            action='store_true',
            help='Wait for async task to complete and show results (only with --async)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        stats_only = options['stats']
        content_type_filter = options.get('content_type')
        use_async = options['async']
        batch_size = options['batch_size']
        wait_for_completion = options['wait']
        
        # Handle async execution
        if use_async:
            self._handle_async_cleanup(
                content_type_filter, batch_size, dry_run, wait_for_completion
            )
            return
        
        # Handle synchronous execution (existing logic)
        if dry_run:
            self.stdout.write("🔍 DRY RUN MODE - No bookmarks will be deleted")
            self.stdout.write("=" * 50)
        elif stats_only:
            self.stdout.write("📊 STATISTICS MODE - Showing orphaned bookmark counts")
            self.stdout.write("=" * 50)
        
        orphaned_bookmarks = self.find_orphaned_bookmarks(content_type_filter)
        
        if not orphaned_bookmarks:
            self.stdout.write(
                self.style.SUCCESS("✅ No orphaned bookmarks found!")
            )
            return
        
        # Group by content type for reporting
        by_content_type = {}
        for bookmark in orphaned_bookmarks:
            ct_name = bookmark.content_type.model
            if ct_name not in by_content_type:
                by_content_type[ct_name] = []
            by_content_type[ct_name].append(bookmark)
        
        # Display statistics
        self.stdout.write("🔍 Orphaned Bookmarks Found:")
        self.stdout.write("-" * 30)
        total_count = 0
        for content_type, bookmarks in by_content_type.items():
            count = len(bookmarks)
            total_count += count
            self.stdout.write(f"  {content_type.title()}: {count} orphaned bookmarks")
        
        self.stdout.write(f"\n📈 Total orphaned bookmarks: {total_count}")
        
        if stats_only:
            return
        
        # Show sample orphaned bookmarks
        if total_count > 0:
            self.stdout.write("\n📋 Sample orphaned bookmarks:")
            self.stdout.write("-" * 35)
            sample_size = min(5, total_count)
            for i, bookmark in enumerate(orphaned_bookmarks[:sample_size]):
                user_email = bookmark.user.email if bookmark.user else "Unknown"
                content_type = bookmark.content_type.model
                note_preview = (bookmark.note[:30] + "...") if bookmark.note and len(bookmark.note) > 30 else (bookmark.note or "No note")
                self.stdout.write(
                    f"  {i+1}. User: {user_email} | Type: {content_type} | ID: {bookmark.object_id} | Note: {note_preview}"
                )
            
            if total_count > sample_size:
                self.stdout.write(f"  ... and {total_count - sample_size} more")
        
        if dry_run:
            self.stdout.write(f"\n🔍 DRY RUN: Would delete {total_count} orphaned bookmarks")
            return
        
        # Confirm deletion
        if total_count > 10:
            self.stdout.write(f"\n⚠️  About to delete {total_count} orphaned bookmarks")
            confirm = input("Continue? (y/N): ")
            if confirm.lower() != 'y':
                self.stdout.write("❌ Operation cancelled")
                return
        
        # Clean up orphaned bookmarks
        self.stdout.write(f"\n🧹 Cleaning up {total_count} orphaned bookmarks...")
        
        deleted_count = 0
        for bookmark in orphaned_bookmarks:
            # Update cache before deleting
            try:
                BookmarkCacheManager.bookmark_deleted(
                    user=bookmark.user,
                    content_type=bookmark.content_type,
                    object_id=bookmark.object_id
                )
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f"Warning: Cache update failed for bookmark {bookmark.id}: {e}")
                )
            
            # Delete the bookmark
            bookmark.delete()
            deleted_count += 1
        
        self.stdout.write(
            self.style.SUCCESS(f"✅ Successfully deleted {deleted_count} orphaned bookmarks!")
        )

    def find_orphaned_bookmarks(self, content_type_filter=None):
        """Find all orphaned bookmarks."""
        orphaned_bookmarks = []
        
        # Get all bookmarks (filtered by content type if specified)
        bookmarks_qs = Bookmark.objects.select_related('content_type', 'user')
        
        if content_type_filter:
            try:
                content_type_filter = content_type_filter.lower()
                bookmarks_qs = bookmarks_qs.filter(
                    content_type__model=content_type_filter
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Invalid content type filter: {content_type_filter}")
                )
                return []
        
        total_bookmarks = bookmarks_qs.count()
        self.stdout.write(f"🔍 Checking {total_bookmarks} bookmarks for orphaned references...")
        
        checked = 0
        for bookmark in bookmarks_qs.iterator():
            checked += 1
            if checked % 100 == 0:
                self.stdout.write(f"  Progress: {checked}/{total_bookmarks}")
            
            # Check if the referenced object exists
            if bookmark.content_object is None:
                orphaned_bookmarks.append(bookmark)
        
        self.stdout.write(f"✅ Checked {checked} bookmarks")
        
        return orphaned_bookmarks

    def get_content_type_stats(self):
        """Get statistics about content types and their bookmarks."""
        stats = {}
        
        # Get all content types that have bookmarks
        content_types = ContentType.objects.filter(
            bookmark__isnull=False
        ).distinct()
        
        for ct in content_types:
            model_class = ct.model_class()
            if model_class is None:
                continue
            
            total_objects = model_class.objects.count()
            total_bookmarks = Bookmark.objects.filter(content_type=ct).count()
            
            # Check for orphaned bookmarks for this content type
            orphaned_count = 0
            for bookmark in Bookmark.objects.filter(content_type=ct):
                if bookmark.content_object is None:
                    orphaned_count += 1
            
            stats[ct.model] = {
                'total_objects': total_objects,
                'total_bookmarks': total_bookmarks,
                'orphaned_bookmarks': orphaned_count,
                'orphan_percentage': (orphaned_count / total_bookmarks * 100) if total_bookmarks > 0 else 0
            }
        
        return stats
    
    def _handle_async_cleanup(self, content_type_filter, batch_size, dry_run, wait_for_completion):
        """Handle asynchronous cleanup using Celery."""
        try:
            from learning_resources.tasks import cleanup_orphaned_bookmarks_async
        except ImportError:
            self.stdout.write(
                self.style.ERROR("❌ Celery tasks not available. Please ensure Celery is installed and configured.")
            )
            return
        
        self.stdout.write("🚀 Starting asynchronous orphaned bookmark cleanup...")
        self.stdout.write(f"  Content Type Filter: {content_type_filter or 'All'}")
        self.stdout.write(f"  Batch Size: {batch_size}")
        self.stdout.write(f"  Dry Run: {dry_run}")
        self.stdout.write("=" * 50)
        
        # Start the async task
        task = cleanup_orphaned_bookmarks_async.delay(
            content_type_filter=content_type_filter,
            batch_size=batch_size,
            dry_run=dry_run
        )
        
        self.stdout.write(f"✅ Task queued with ID: {task.id}")
        
        if not wait_for_completion:
            self.stdout.write("\n📝 To check task status, run:")
            self.stdout.write(f"   python manage.py shell -c \"from celery.result import AsyncResult; print(AsyncResult('{task.id}').status)\"")
            self.stdout.write("\n💡 Use --wait flag to wait for completion in this command.")
            return
        
        # Wait for task completion and show progress
        self.stdout.write("\n⏳ Waiting for task completion...")
        
        last_progress = 0
        while not task.ready():
            try:
                result = task.result
                if isinstance(result, dict) and 'current' in result:
                    current = result['current']
                    total = result['total']
                    progress_percent = result.get('progress_percent', 0)
                    
                    if progress_percent > last_progress:
                        self.stdout.write(f"   Progress: {current}/{total} ({progress_percent}%)")
                        last_progress = progress_percent
                
            except Exception:
                # Task might not have progress info yet
                pass
            
            time.sleep(2)  # Check every 2 seconds
        
        # Task completed, show results
        try:
            result = task.get()  # This will raise an exception if task failed
            
            self.stdout.write("\n" + "=" * 50)
            self.stdout.write("🎉 ASYNC CLEANUP COMPLETED")
            self.stdout.write("=" * 50)
            
            self.stdout.write(f"📊 Task ID: {result['task_id']}")
            self.stdout.write(f"⏱️  Duration: {result['duration_seconds']:.2f} seconds")
            self.stdout.write(f"📈 Total Processed: {result['total_processed']}")
            self.stdout.write(f"🔍 Orphaned Found: {result['orphaned_found']}")
            
            if result['dry_run']:
                self.stdout.write(f"🧪 DRY RUN: Would delete {result['orphaned_found']} bookmarks")
            else:
                self.stdout.write(f"🗑️  Deleted: {result['deleted']}")
            
            # Show breakdown by content type
            if result['orphaned_by_type']:
                self.stdout.write("\n📋 Breakdown by content type:")
                for content_type, count in result['orphaned_by_type'].items():
                    self.stdout.write(f"  {content_type.title()}: {count}")
            
            self.stdout.write(f"\n🔧 Batch Size: {result['batch_size']}")
            
        except Exception as e:
            self.stdout.write(f"\n❌ Task failed: {str(e)}")
            
            # Try to get task state for more info
            try:
                if hasattr(task, 'state') and task.state == 'FAILURE':
                    self.stdout.write(f"📄 Error details: {task.info}")
            except Exception:
                pass