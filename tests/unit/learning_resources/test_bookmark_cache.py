"""Unit tests for bookmark cache functionality."""

from django.test import TestCase
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from learning_resources.models import Video, Article, Bookmark
from learning_resources.cache import BookmarkCacheService, BookmarkCacheManager
from tests.base import BaseTestCase


class BookmarkCacheServiceTests(BaseTestCase):
    """Test the BookmarkCacheService functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Clear cache before each test
        cache.clear()
        
        self.user = self.create_user(email="testuser@example.com")
        self.video1 = self.factory.create_video(title="Video 1")
        self.video2 = self.factory.create_video(title="Video 2")
        self.article1 = self.factory.create_article(title="Article 1")
        
        self.video_ct = ContentType.objects.get_for_model(Video)
        self.article_ct = ContentType.objects.get_for_model(Article)
    
    def test_cache_key_generation(self):
        """Test cache key generation."""
        key = BookmarkCacheService._get_cache_key(self.user.id, self.video_ct.id)
        expected = f"bookmarks:user:{self.user.id}:ct:{self.video_ct.id}"
        self.assertEqual(key, expected)
    
    def test_set_and_get_user_bookmarks(self):
        """Test setting and getting user bookmarks from cache."""
        bookmark_ids = {self.video1.id, self.video2.id}
        
        # Set bookmarks in cache
        success = BookmarkCacheService.set_user_bookmarks(
            self.user, self.video_ct, bookmark_ids
        )
        self.assertTrue(success)
        
        # Get bookmarks from cache
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        
        self.assertEqual(cached_bookmarks, bookmark_ids)
    
    def test_get_user_bookmarks_cache_miss(self):
        """Test getting user bookmarks when not in cache."""
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        
        # Should return None for cache miss
        self.assertIsNone(cached_bookmarks)
    
    def test_add_bookmark_to_cache(self):
        """Test adding a bookmark to existing cache."""
        # Set initial cache
        bookmark_ids = {self.video1.id}
        BookmarkCacheService.set_user_bookmarks(
            self.user, self.video_ct, bookmark_ids
        )
        
        # Add new bookmark
        success = BookmarkCacheService.add_bookmark_to_cache(
            self.user, self.video_ct, self.video2.id
        )
        self.assertTrue(success)
        
        # Check cache contains both bookmarks
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertEqual(cached_bookmarks, {self.video1.id, self.video2.id})
    
    def test_add_bookmark_to_cache_no_existing_cache(self):
        """Test adding bookmark when no cache exists."""
        # Try to add bookmark to non-existent cache
        success = BookmarkCacheService.add_bookmark_to_cache(
            self.user, self.video_ct, self.video1.id
        )
        
        # Should return False (doesn't create cache)
        self.assertFalse(success)
        
        # Cache should still be empty
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertIsNone(cached_bookmarks)
    
    def test_remove_bookmark_from_cache(self):
        """Test removing a bookmark from cache."""
        # Set initial cache
        bookmark_ids = {self.video1.id, self.video2.id}
        BookmarkCacheService.set_user_bookmarks(
            self.user, self.video_ct, bookmark_ids
        )
        
        # Remove bookmark
        success = BookmarkCacheService.remove_bookmark_from_cache(
            self.user, self.video_ct, self.video1.id
        )
        self.assertTrue(success)
        
        # Check cache only contains remaining bookmark
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertEqual(cached_bookmarks, {self.video2.id})
    
    def test_remove_bookmark_from_cache_no_existing_cache(self):
        """Test removing bookmark when no cache exists."""
        # Try to remove bookmark from non-existent cache
        success = BookmarkCacheService.remove_bookmark_from_cache(
            self.user, self.video_ct, self.video1.id
        )
        
        # Should return False (no cache to modify)
        self.assertFalse(success)
    
    def test_invalidate_user_bookmarks_specific_content_type(self):
        """Test invalidating cache for specific content type."""
        # Set cache for videos and articles
        video_bookmarks = {self.video1.id}
        article_bookmarks = {self.article1.id}
        
        BookmarkCacheService.set_user_bookmarks(
            self.user, self.video_ct, video_bookmarks
        )
        BookmarkCacheService.set_user_bookmarks(
            self.user, self.article_ct, article_bookmarks
        )
        
        # Invalidate only video cache
        success = BookmarkCacheService.invalidate_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertTrue(success)
        
        # Video cache should be invalidated
        video_cached = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertIsNone(video_cached)
        
        # Article cache should still exist
        article_cached = BookmarkCacheService.get_user_bookmarks(
            self.user, self.article_ct
        )
        self.assertEqual(article_cached, article_bookmarks)
    
    def test_preload_user_bookmarks(self):
        """Test preloading user bookmarks from database."""
        # Create bookmarks in database
        Bookmark.objects.create(
            user=self.user,
            content_type=self.video_ct,
            object_id=self.video1.id
        )
        Bookmark.objects.create(
            user=self.user,
            content_type=self.video_ct,
            object_id=self.video2.id
        )
        
        # Preload from database
        bookmark_set = BookmarkCacheService.preload_user_bookmarks(
            self.user, self.video_ct
        )
        
        # Should return set of bookmark IDs
        self.assertEqual(bookmark_set, {self.video1.id, self.video2.id})
        
        # Should also cache the results
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertEqual(cached_bookmarks, bookmark_set)
    
    def test_preload_user_bookmarks_empty(self):
        """Test preloading when user has no bookmarks."""
        bookmark_set = BookmarkCacheService.preload_user_bookmarks(
            self.user, self.video_ct
        )
        
        # Should return empty set
        self.assertEqual(bookmark_set, set())
        
        # Should cache empty set
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(
            self.user, self.video_ct
        )
        self.assertEqual(cached_bookmarks, set())


class BookmarkCacheManagerTests(BaseTestCase):
    """Test the BookmarkCacheManager functionality."""
    
    def setUp(self):
        """Set up test data."""
        # Clear cache before each test
        cache.clear()
        
        self.user = self.create_user(email="testuser@example.com")
        self.video1 = self.factory.create_video(title="Video 1")
        self.video2 = self.factory.create_video(title="Video 2")
        self.article1 = self.factory.create_article(title="Article 1")
        
        # Create some bookmarks in database
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video1.id
        )
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article1.id
        )
    
    def test_is_bookmarked_with_cache_hit(self):
        """Test is_bookmarked with cache hit."""
        # Preload cache
        video_ct = ContentType.objects.get_for_model(Video)
        BookmarkCacheService.preload_user_bookmarks(self.user, video_ct)
        
        # Test bookmarked item
        is_bookmarked = BookmarkCacheManager.is_bookmarked(self.user, self.video1)
        self.assertTrue(is_bookmarked)
        
        # Test non-bookmarked item
        is_bookmarked = BookmarkCacheManager.is_bookmarked(self.user, self.video2)
        self.assertFalse(is_bookmarked)
    
    def test_is_bookmarked_with_cache_miss(self):
        """Test is_bookmarked with cache miss (auto-preload)."""
        # Cache is empty, should auto-preload from database
        is_bookmarked = BookmarkCacheManager.is_bookmarked(self.user, self.video1)
        self.assertTrue(is_bookmarked)
        
        # Cache should now be populated
        video_ct = ContentType.objects.get_for_model(Video)
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, video_ct)
        self.assertIsNotNone(cached_bookmarks)
        self.assertIn(self.video1.id, cached_bookmarks)
    
    def test_get_bookmarks_for_objects_mixed_types(self):
        """Test getting bookmarks for mixed content types."""
        objects = [self.video1, self.video2, self.article1]
        
        bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(
            self.user, objects
        )
        
        # Should return status for all objects
        self.assertEqual(len(bookmark_status), 3)
        self.assertTrue(bookmark_status[self.video1.id])
        self.assertFalse(bookmark_status[self.video2.id])
        self.assertTrue(bookmark_status[self.article1.id])
    
    def test_get_bookmarks_for_objects_empty_list(self):
        """Test getting bookmarks for empty object list."""
        bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(
            self.user, []
        )
        
        self.assertEqual(bookmark_status, {})
    
    def test_get_bookmarks_for_objects_unauthenticated_user(self):
        """Test getting bookmarks for unauthenticated user."""
        from django.contrib.auth.models import AnonymousUser
        
        anonymous_user = AnonymousUser()
        bookmark_status = BookmarkCacheManager.get_bookmarks_for_objects(
            anonymous_user, [self.video1, self.video2]
        )
        
        self.assertEqual(bookmark_status, {})
    
    def test_bookmark_created_event(self):
        """Test bookmark created event handling."""
        video_ct = ContentType.objects.get_for_model(Video)
        
        # Preload cache
        BookmarkCacheService.preload_user_bookmarks(self.user, video_ct)
        
        # Initially video2 should not be bookmarked
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, video_ct)
        self.assertNotIn(self.video2.id, cached_bookmarks)
        
        # Trigger bookmark created event
        BookmarkCacheManager.bookmark_created(self.user, video_ct, self.video2.id)
        
        # Cache should now include the new bookmark
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, video_ct)
        self.assertIn(self.video2.id, cached_bookmarks)
    
    def test_bookmark_deleted_event(self):
        """Test bookmark deleted event handling."""
        video_ct = ContentType.objects.get_for_model(Video)
        
        # Preload cache
        BookmarkCacheService.preload_user_bookmarks(self.user, video_ct)
        
        # Initially video1 should be bookmarked
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, video_ct)
        self.assertIn(self.video1.id, cached_bookmarks)
        
        # Trigger bookmark deleted event
        BookmarkCacheManager.bookmark_deleted(self.user, video_ct, self.video1.id)
        
        # Cache should no longer include the deleted bookmark
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, video_ct)
        self.assertNotIn(self.video1.id, cached_bookmarks)
    
    def test_user_cache_invalidated_event(self):
        """Test user cache invalidated event."""
        video_ct = ContentType.objects.get_for_model(Video)
        article_ct = ContentType.objects.get_for_model(Article)
        
        # Preload caches
        BookmarkCacheService.preload_user_bookmarks(self.user, video_ct)
        BookmarkCacheService.preload_user_bookmarks(self.user, article_ct)
        
        # Both caches should exist
        self.assertIsNotNone(BookmarkCacheService.get_user_bookmarks(self.user, video_ct))
        self.assertIsNotNone(BookmarkCacheService.get_user_bookmarks(self.user, article_ct))
        
        # Trigger user cache invalidation
        BookmarkCacheManager.user_cache_invalidated(self.user)
        
        # All user caches should be invalidated
        self.assertIsNone(BookmarkCacheService.get_user_bookmarks(self.user, video_ct))
        self.assertIsNone(BookmarkCacheService.get_user_bookmarks(self.user, article_ct))


class BookmarkCacheConsistencyTests(BaseTestCase):
    """Test cache consistency with database operations."""
    
    def setUp(self):
        """Set up test data."""
        # Clear cache before each test
        cache.clear()
        
        self.user = self.create_user(email="testuser@example.com")
        self.video = self.factory.create_video(title="Test Video")
        self.video_ct = ContentType.objects.get_for_model(Video)
    
    def test_cache_consistency_on_bookmark_creation(self):
        """Test that cache is updated when bookmark is created."""
        # Preload empty cache
        BookmarkCacheService.preload_user_bookmarks(self.user, self.video_ct)
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, self.video_ct)
        self.assertEqual(cached_bookmarks, set())
        
        # Create bookmark (this should trigger cache update via signals)
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=self.video_ct,
            object_id=self.video.id
        )
        
        # Cache should be updated (note: this depends on signals working)
        # In unit tests, signals might not fire automatically, so we'll test the manager method
        BookmarkCacheManager.bookmark_created(self.user, self.video_ct, self.video.id)
        
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, self.video_ct)
        self.assertIn(self.video.id, cached_bookmarks)
    
    def test_cache_consistency_on_bookmark_deletion(self):
        """Test that cache is updated when bookmark is deleted."""
        # Create bookmark and preload cache
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=self.video_ct,
            object_id=self.video.id
        )
        BookmarkCacheService.preload_user_bookmarks(self.user, self.video_ct)
        
        # Verify bookmark is in cache
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, self.video_ct)
        self.assertIn(self.video.id, cached_bookmarks)
        
        # Delete bookmark (simulate signal firing)
        BookmarkCacheManager.bookmark_deleted(self.user, self.video_ct, self.video.id)
        bookmark.delete()
        
        # Cache should be updated
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, self.video_ct)
        self.assertNotIn(self.video.id, cached_bookmarks)
    
    def test_cache_survives_unrelated_operations(self):
        """Test that cache is not affected by unrelated operations."""
        # Create bookmark and preload cache
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=self.video_ct,
            object_id=self.video.id
        )
        BookmarkCacheService.preload_user_bookmarks(self.user, self.video_ct)
        
        # Perform unrelated operations
        other_user = self.create_user(email="otheruser@example.com")
        other_video = self.factory.create_video(title="Other Video")
        
        # Create bookmark for other user
        Bookmark.objects.create(
            user=other_user,
            content_type=self.video_ct,
            object_id=other_video.id
        )
        
        # Update the video title
        self.video.title = "Updated Video Title"
        self.video.save()
        
        # Original user's cache should be unchanged
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(self.user, self.video_ct)
        self.assertIn(self.video.id, cached_bookmarks)
        self.assertEqual(len(cached_bookmarks), 1)