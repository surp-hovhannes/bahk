"""
Django management command for bookmark cache operations.

This command provides utilities for monitoring, debugging, and managing
the Redis bookmark cache system.

Usage:
    python manage.py bookmark_cache stats          # Show cache statistics
    python manage.py bookmark_cache clear          # Clear all bookmark cache
    python manage.py bookmark_cache warm           # Warm up cache for active users
    python manage.py bookmark_cache test           # Test cache performance
"""

import time
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import models
from learning_resources.models import Video, Article, Recipe, Bookmark
from learning_resources.cache import BookmarkCacheService, BookmarkCacheManager
from hub.models import DevotionalSet


class Command(BaseCommand):
    help = 'Manage bookmark Redis cache operations'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['stats', 'clear', 'warm', 'test'],
            help='Action to perform on bookmark cache'
        )
        
        parser.add_argument(
            '--user-id',
            type=int,
            help='Specific user ID for cache operations'
        )
        
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Limit number of users for warming (default: 100)'
        )

    def handle(self, *args, **options):
        action = options['action']
        
        if action == 'stats':
            self.show_stats()
        elif action == 'clear':
            self.clear_cache(options.get('user_id'))
        elif action == 'warm':
            self.warm_cache(options.get('user_id'), options['limit'])
        elif action == 'test':
            self.test_performance()

    def show_stats(self):
        """Display cache statistics."""
        self.stdout.write("📊 Bookmark Cache Statistics")
        self.stdout.write("=" * 50)
        
        # Get cache stats
        stats = BookmarkCacheService.get_cache_stats()
        
        if 'error' in stats:
            self.stdout.write(
                self.style.ERROR(f"❌ Cache unavailable: {stats['error']}")
            )
            return
        
        # Display statistics
        self.stdout.write(f"🔑 Total bookmark keys: {stats.get('total_keys', 'Unknown')}")
        self.stdout.write(f"💾 Memory used: {stats.get('memory_used', 'Unknown')}")
        self.stdout.write(f"🎯 Hit ratio: {stats.get('hit_ratio', 'Unknown')}")
        self.stdout.write(f"⏰ Cache TTL: {stats.get('cache_ttl', 'Unknown')} seconds")
        self.stdout.write(f"🏷️  Cache prefix: {stats.get('cache_prefix', 'Unknown')}")
        
        # Show database statistics
        self.stdout.write("\n📈 Database Statistics")
        self.stdout.write("-" * 30)
        total_bookmarks = Bookmark.objects.count()
        total_users_with_bookmarks = Bookmark.objects.values('user').distinct().count()
        
        self.stdout.write(f"📚 Total bookmarks: {total_bookmarks}")
        self.stdout.write(f"👥 Users with bookmarks: {total_users_with_bookmarks}")
        
        # Show content type breakdown
        content_type_stats = (Bookmark.objects
                            .values('content_type__model')
                            .annotate(count=models.Count('id'))
                            .order_by('-count'))
        
        self.stdout.write("\n📊 Bookmarks by Content Type")
        self.stdout.write("-" * 35)
        for stat in content_type_stats:
            model_name = stat['content_type__model'].title()
            count = stat['count']
            self.stdout.write(f"  {model_name}: {count}")

    def clear_cache(self, user_id=None):
        """Clear bookmark cache."""
        if user_id:
            try:
                user = User.objects.get(id=user_id)
                BookmarkCacheService.invalidate_user_bookmarks(user)
                self.stdout.write(
                    self.style.SUCCESS(f"✅ Cleared cache for user {user_id}")
                )
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"❌ User {user_id} not found")
                )
        else:
            # Clear all bookmark caches (this is a simplified approach)
            # In production, you might want to use Redis SCAN for pattern deletion
            try:
                from django.core.cache import cache
                # This clears the entire cache - use with caution
                cache.clear()
                self.stdout.write(
                    self.style.WARNING("🧹 Cleared entire cache (all bookmark caches removed)")
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"❌ Failed to clear cache: {e}")
                )

    def warm_cache(self, user_id=None, limit=100):
        """Warm up bookmark cache for active users."""
        self.stdout.write("🔥 Warming bookmark cache...")
        
        if user_id:
            users = User.objects.filter(id=user_id)
            if not users.exists():
                self.stdout.write(
                    self.style.ERROR(f"❌ User {user_id} not found")
                )
                return
        else:
            # Get users who have bookmarks, ordered by recent activity
            users = (User.objects
                    .filter(bookmarks__isnull=False)
                    .distinct()
                    .order_by('-last_login')[:limit])
        
        content_types = [
            ContentType.objects.get_for_model(Video),
            ContentType.objects.get_for_model(Article),
            ContentType.objects.get_for_model(Recipe),
            ContentType.objects.get_for_model(DevotionalSet),
        ]
        
        warmed_count = 0
        for user in users:
            for content_type in content_types:
                BookmarkCacheService.preload_user_bookmarks(user, content_type)
                warmed_count += 1
        
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Warmed cache for {len(users)} users "
                f"across {len(content_types)} content types "
                f"({warmed_count} cache entries created)"
            )
        )

    def test_performance(self):
        """Test cache vs database performance."""
        self.stdout.write("🏎️  Testing bookmark lookup performance...")
        self.stdout.write("=" * 50)
        
        # Get a user with bookmarks
        user = User.objects.filter(bookmarks__isnull=False).first()
        if not user:
            self.stdout.write(
                self.style.WARNING("⚠️  No users with bookmarks found for testing")
            )
            return
        
        # Get some videos to test with
        videos = list(Video.objects.all()[:20])
        if not videos:
            self.stdout.write(
                self.style.WARNING("⚠️  No videos found for testing")
            )
            return
        
        # Test database lookup
        start_time = time.time()
        for video in videos:
            Bookmark.objects.filter(
                user=user,
                content_type=ContentType.objects.get_for_model(Video),
                object_id=video.id
            ).exists()
        db_time = time.time() - start_time
        
        # Test cache lookup
        start_time = time.time()
        for video in videos:
            BookmarkCacheManager.is_bookmarked(user, video)
        cache_time = time.time() - start_time
        
        # Test batch cache lookup
        start_time = time.time()
        BookmarkCacheManager.get_bookmarks_for_objects(user, videos)
        batch_cache_time = time.time() - start_time
        
        # Display results
        self.stdout.write(f"📊 Performance Test Results ({len(videos)} videos)")
        self.stdout.write("-" * 45)
        self.stdout.write(f"🐌 Database lookup: {db_time:.4f}s ({db_time/len(videos)*1000:.2f}ms per item)")
        self.stdout.write(f"⚡ Cache lookup: {cache_time:.4f}s ({cache_time/len(videos)*1000:.2f}ms per item)")
        self.stdout.write(f"🚀 Batch cache lookup: {batch_cache_time:.4f}s ({batch_cache_time/len(videos)*1000:.2f}ms per item)")
        
        if db_time > 0:
            speedup_individual = db_time / cache_time if cache_time > 0 else float('inf')
            speedup_batch = db_time / batch_cache_time if batch_cache_time > 0 else float('inf')
            
            self.stdout.write(f"\n🎯 Performance Improvement:")
            self.stdout.write(f"   Individual cache: {speedup_individual:.1f}x faster")
            self.stdout.write(f"   Batch cache: {speedup_batch:.1f}x faster")
        
        self.stdout.write(
            self.style.SUCCESS("\n✅ Performance test completed!")
        )