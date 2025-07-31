"""Performance tests for bookmark caching functionality."""

import time
from django.test import TestCase
from django.test.utils import tag
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from learning_resources.models import Video, Article, Recipe, Bookmark
from learning_resources.cache import BookmarkCacheManager, BookmarkCacheService
from tests.base import BaseTestCase


@tag('performance')
class BookmarkCachePerformanceTests(BaseTestCase):
    """Test bookmark cache performance improvements."""
    
    def setUp(self):
        """Set up test data."""
        self.user = self.create_user(email="testuser@example.com")
        
        # Create multiple videos for performance testing
        self.videos = []
        for i in range(20):
            video = self.factory.create_video(title=f"Performance Test Video {i+1}")
            self.videos.append(video)
        
        # Create some bookmarks (bookmark every other video)
        self.bookmarked_videos = []
        for i in range(0, 20, 2):
            Bookmark.objects.create(
                user=self.user,
                content_type=ContentType.objects.get_for_model(Video),
                object_id=self.videos[i].id,
                note=f"Bookmark {i+1}"
            )
            self.bookmarked_videos.append(self.videos[i])
    
    def test_database_vs_cache_performance(self):
        """Compare database lookup vs cache performance."""
        content_type = ContentType.objects.get_for_model(Video)
        
        # Measure database lookup time (cold)
        start_time = time.time()
        for video in self.videos:
            Bookmark.objects.filter(
                user=self.user,
                content_type=content_type,
                object_id=video.id
            ).exists()
        db_time = time.time() - start_time
        
        # Warm up cache
        BookmarkCacheService.preload_user_bookmarks(self.user, content_type)
        
        # Measure cache lookup time (warm)
        start_time = time.time()
        for video in self.videos:
            BookmarkCacheManager.is_bookmarked(self.user, video)
        cache_time = time.time() - start_time
        
        # Cache should be significantly faster
        print(f"\nDatabase lookup time: {db_time:.4f}s")
        print(f"Cache lookup time: {cache_time:.4f}s")
        print(f"Speed improvement: {db_time/cache_time if cache_time > 0 else 'infinite'}x")
        
        # Performance assertion (cache should be at least 2x faster)
        if cache_time > 0:
            self.assertGreater(db_time / cache_time, 2.0, "Cache should be at least 2x faster than database")
        
        # Individual lookups should complete quickly
        self.assertLess(cache_time, 0.05, "Cache lookups should complete in < 50ms total")
    
    def test_batch_cache_performance(self):
        """Test batch cache lookup performance."""
        # Measure batch cache lookup time
        start_time = time.time()
        bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(self.user, self.videos)
        batch_time = time.time() - start_time
        
        print(f"\nBatch cache lookup time for {len(self.videos)} videos: {batch_time:.4f}s")
        print(f"Per-item time: {batch_time/len(self.videos)*1000:.2f}ms")
        
        # Verify correctness
        self.assertEqual(len(bookmark_status), len(self.videos))
        
        # Check that bookmarked videos are correctly identified
        for video in self.bookmarked_videos:
            self.assertTrue(bookmark_status[video.id], f"Video {video.id} should be bookmarked")
        
        # Check that non-bookmarked videos are correctly identified
        for video in self.videos:
            if video not in self.bookmarked_videos:
                self.assertFalse(bookmark_status[video.id], f"Video {video.id} should not be bookmarked")
        
        # Performance assertion
        self.assertLess(batch_time, 0.1, "Batch lookup should complete in < 100ms")
        self.assertLess(batch_time/len(self.videos), 0.005, "Per-item batch lookup should be < 5ms")
    
    def test_cache_warmup_performance(self):
        """Test cache warmup performance."""
        content_type = ContentType.objects.get_for_model(Video)
        
        # Clear cache first
        BookmarkCacheService.invalidate_user_bookmarks(self.user, content_type)
        
        # Measure warmup time
        start_time = time.time()
        bookmark_set = BookmarkCacheService.preload_user_bookmarks(self.user, content_type)
        warmup_time = time.time() - start_time
        
        print(f"\nCache warmup time: {warmup_time:.4f}s")
        print(f"Bookmarks loaded: {len(bookmark_set)}")
        
        # Verify correctness
        expected_bookmarks = len(self.bookmarked_videos)
        self.assertEqual(len(bookmark_set), expected_bookmarks)
        
        # Performance assertion
        self.assertLess(warmup_time, 0.2, "Cache warmup should complete in < 200ms")
    
    def test_cache_update_performance(self):
        """Test cache update performance when bookmarks change."""
        content_type = ContentType.objects.get_for_model(Video)
        
        # Warm up cache
        BookmarkCacheService.preload_user_bookmarks(self.user, content_type)
        
        # Test adding bookmark to cache
        unbookmarked_video = self.videos[-1]  # Last video should not be bookmarked
        
        start_time = time.time()
        BookmarkCacheService.add_bookmark_to_cache(
            self.user, content_type, unbookmarked_video.id
        )
        add_time = time.time() - start_time
        
        # Test removing bookmark from cache
        bookmarked_video = self.bookmarked_videos[0]
        
        start_time = time.time()
        BookmarkCacheService.remove_bookmark_from_cache(
            self.user, content_type, bookmarked_video.id
        )
        remove_time = time.time() - start_time
        
        print(f"\nCache add time: {add_time:.4f}s")
        print(f"Cache remove time: {remove_time:.4f}s")
        
        # Performance assertions
        self.assertLess(add_time, 0.01, "Cache add should complete in < 10ms")
        self.assertLess(remove_time, 0.01, "Cache remove should complete in < 10ms")
        
        # Verify correctness
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, content_type)
        self.assertIn(unbookmarked_video.id, cached_bookmarks)
        self.assertNotIn(bookmarked_video.id, cached_bookmarks)


@tag('performance', 'slow')
class BookmarkCacheLoadTests(BaseTestCase):
    """Load testing for bookmark cache under stress."""
    
    def setUp(self):
        """Set up large dataset for load testing."""
        self.users = []
        self.videos = []
        
        # Create multiple users
        for i in range(5):
            user = self.create_user(email=f"loadtest{i}@example.com")
            self.users.append(user)
        
        # Create many videos
        for i in range(100):
            video = self.factory.create_video(title=f"Load Test Video {i+1}")
            self.videos.append(video)
        
        # Create bookmarks for each user (different patterns)
        content_type = ContentType.objects.get_for_model(Video)
        for user_idx, user in enumerate(self.users):
            # Each user bookmarks different videos
            start_idx = user_idx * 10
            end_idx = start_idx + 20
            for video_idx in range(start_idx, min(end_idx, len(self.videos))):
                Bookmark.objects.create(
                    user=user,
                    content_type=content_type,
                    object_id=self.videos[video_idx].id
                )
    
    def test_multiple_users_cache_performance(self):
        """Test cache performance with multiple users."""
        content_type = ContentType.objects.get_for_model(Video)
        
        # Test batch operations for all users
        start_time = time.time()
        
        all_results = {}
        for user in self.users:
            bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(
                user, self.videos[:20]  # Test with first 20 videos
            )
            all_results[user.id] = bookmark_status
        
        total_time = time.time() - start_time
        
        print(f"\nMultiple users ({len(self.users)}) cache lookup time: {total_time:.4f}s")
        print(f"Average per user: {total_time/len(self.users):.4f}s")
        print(f"Total lookups: {len(self.users) * 20}")
        print(f"Average per lookup: {total_time/(len(self.users) * 20)*1000:.2f}ms")
        
        # Verify all users got results
        self.assertEqual(len(all_results), len(self.users))
        for user_id, results in all_results.items():
            self.assertEqual(len(results), 20)
        
        # Performance assertion
        self.assertLess(total_time, 1.0, "Multiple user lookups should complete in < 1 second")
        self.assertLess(total_time/len(self.users), 0.2, "Per-user lookup should be < 200ms")
    
    def test_cache_scalability_with_many_bookmarks(self):
        """Test cache performance with users who have many bookmarks."""
        # Create a user with many bookmarks
        heavy_user = self.create_user(email="heavyuser@example.com")
        
        # Create many bookmarks for this user
        content_type = ContentType.objects.get_for_model(Video)
        for video in self.videos:  # Bookmark all videos
            Bookmark.objects.create(
                user=heavy_user,
                content_type=content_type,
                object_id=video.id
            )
        
        # Test cache operations with heavy user
        start_time = time.time()
        bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(
            heavy_user, self.videos
        )
        heavy_user_time = time.time() - start_time
        
        print(f"\nHeavy user ({len(self.videos)} bookmarks) lookup time: {heavy_user_time:.4f}s")
        print(f"Per-bookmark lookup time: {heavy_user_time/len(self.videos)*1000:.2f}ms")
        
        # Verify all bookmarks are found
        self.assertEqual(len(bookmark_status), len(self.videos))
        for video in self.videos:
            self.assertTrue(bookmark_status[video.id], f"Video {video.id} should be bookmarked")
        
        # Performance assertion (should still be fast even with many bookmarks)
        self.assertLess(heavy_user_time, 0.5, "Heavy user lookup should complete in < 500ms")
        self.assertLess(heavy_user_time/len(self.videos), 0.005, "Per-bookmark lookup should be < 5ms")
    
    def test_concurrent_cache_operations(self):
        """Test cache performance under concurrent operations."""
        import threading
        import queue
        
        results_queue = queue.Queue()
        content_type = ContentType.objects.get_for_model(Video)
        
        def worker(user, video_subset):
            """Worker function for concurrent testing."""
            start_time = time.time()
            bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(
                user, video_subset
            )
            end_time = time.time()
            results_queue.put({
                'user_id': user.id,
                'time': end_time - start_time,
                'results': len(bookmark_status)
            })
        
        # Create threads for concurrent access
        threads = []
        for i, user in enumerate(self.users):
            video_subset = self.videos[i*10:(i+1)*10]  # Different video subset per user
            thread = threading.Thread(target=worker, args=(user, video_subset))
            threads.append(thread)
        
        # Start all threads
        start_time = time.time()
        for thread in threads:
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        total_time = time.time() - start_time
        
        # Collect results
        results = []
        while not results_queue.empty():
            results.append(results_queue.get())
        
        print(f"\nConcurrent operations ({len(threads)} threads) total time: {total_time:.4f}s")
        print(f"Average thread time: {sum(r['time'] for r in results)/len(results):.4f}s")
        
        # Verify all threads completed successfully
        self.assertEqual(len(results), len(self.users))
        
        # Performance assertion
        self.assertLess(total_time, 2.0, "Concurrent operations should complete in < 2 seconds")
        
        # No thread should take too long
        max_thread_time = max(r['time'] for r in results)
        self.assertLess(max_thread_time, 1.0, "No single thread should take > 1 second")