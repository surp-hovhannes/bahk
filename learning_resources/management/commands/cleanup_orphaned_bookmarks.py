"""
Django management command to clean up orphaned bookmarks.

This command finds and removes bookmarks that point to non-existent objects,
which can happen if objects were deleted before the signal handlers were in place.

Usage:
    python manage.py cleanup_orphaned_bookmarks           # Clean up all orphaned bookmarks
    python manage.py cleanup_orphaned_bookmarks --dry-run # Show what would be deleted
    python manage.py cleanup_orphaned_bookmarks --stats   # Show statistics only
"""

from django.core.management.base import BaseCommand
from django.contrib.contenttypes.models import ContentType
from learning_resources.models import Bookmark
from learning_resources.cache import BookmarkCacheManager


class Command(BaseCommand):
    help = 'Clean up orphaned bookmarks that point to non-existent objects'

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

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        stats_only = options['stats']
        content_type_filter = options.get('content_type')
        
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