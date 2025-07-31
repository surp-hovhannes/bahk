"""Unit tests for bookmark signal handlers."""

from django.test import TestCase
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from learning_resources.models import Video, Article, Recipe, Bookmark
from hub.models import DevotionalSet
from tests.base import BaseTestCase


class BookmarkSignalTests(BaseTestCase):
    """Test cases for bookmark signal handlers that prevent orphaned bookmarks."""
    
    def setUp(self):
        """Set up test data."""
        self.user1 = self.create_user(email="user1@example.com")
        self.user2 = self.create_user(email="user2@example.com")
        
        # Create content to bookmark
        self.video = self.factory.create_video(title="Test Video")
        self.article = self.factory.create_article(title="Test Article")
        self.recipe = self.factory.create_recipe(title="Test Recipe")
        self.devotional_set = self.factory.create_devotional_set(title="Test Devotional Set")
    
    def test_video_deletion_removes_bookmarks(self):
        """Test that deleting a video removes associated bookmarks."""
        # Create bookmarks for the video from multiple users
        bookmark1 = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note="Great video!"
        )
        bookmark2 = Bookmark.objects.create(
            user=self.user2,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note="Helpful content"
        )
        
        # Verify bookmarks exist
        self.assertEqual(Bookmark.objects.filter(object_id=self.video.id).count(), 2)
        
        # Delete the video - this should trigger the signal
        video_id = self.video.id
        self.video.delete()
        
        # Verify bookmarks are removed
        self.assertEqual(Bookmark.objects.filter(object_id=video_id).count(), 0)
        
        # Verify other bookmarks are unaffected
        other_bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id
        )
        self.assertTrue(Bookmark.objects.filter(id=other_bookmark.id).exists())
    
    def test_article_deletion_removes_bookmarks(self):
        """Test that deleting an article removes associated bookmarks."""
        # Create bookmark for the article
        bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id,
            note="Useful article"
        )
        
        # Verify bookmark exists
        self.assertTrue(Bookmark.objects.filter(id=bookmark.id).exists())
        
        # Delete the article
        article_id = self.article.id
        self.article.delete()
        
        # Verify bookmark is removed
        self.assertFalse(Bookmark.objects.filter(object_id=article_id).exists())
    
    def test_recipe_deletion_removes_bookmarks(self):
        """Test that deleting a recipe removes associated bookmarks."""
        # Create bookmark for the recipe
        bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Recipe),
            object_id=self.recipe.id,
            note="Delicious recipe"
        )
        
        # Verify bookmark exists
        self.assertTrue(Bookmark.objects.filter(id=bookmark.id).exists())
        
        # Delete the recipe
        recipe_id = self.recipe.id
        self.recipe.delete()
        
        # Verify bookmark is removed
        self.assertFalse(Bookmark.objects.filter(object_id=recipe_id).exists())
    
    def test_devotional_set_deletion_removes_bookmarks(self):
        """Test that deleting a devotional set removes associated bookmarks."""
        # Create bookmark for the devotional set
        bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(DevotionalSet),
            object_id=self.devotional_set.id,
            note="Inspiring devotionals"
        )
        
        # Verify bookmark exists
        self.assertTrue(Bookmark.objects.filter(id=bookmark.id).exists())
        
        # Delete the devotional set
        devotional_set_id = self.devotional_set.id
        self.devotional_set.delete()
        
        # Verify bookmark is removed
        self.assertFalse(Bookmark.objects.filter(object_id=devotional_set_id).exists())
    
    def test_multiple_bookmarks_cleaned_up(self):
        """Test that all bookmarks for a deleted object are cleaned up."""
        # Create multiple bookmarks for the same video
        bookmarks = []
        for i in range(5):
            user = self.create_user(email=f"user{i+3}@example.com")
            bookmark = Bookmark.objects.create(
                user=user,
                content_type=ContentType.objects.get_for_model(Video),
                object_id=self.video.id,
                note=f"Bookmark {i+1}"
            )
            bookmarks.append(bookmark)
        
        # Verify all bookmarks exist
        self.assertEqual(Bookmark.objects.filter(object_id=self.video.id).count(), 5)
        
        # Delete the video
        video_id = self.video.id
        self.video.delete()
        
        # Verify all bookmarks are removed
        self.assertEqual(Bookmark.objects.filter(object_id=video_id).count(), 0)
    
    def test_user_deletion_removes_bookmarks(self):
        """Test that deleting a user removes their bookmarks (built-in CASCADE)."""
        # Create bookmarks for the user
        bookmark1 = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        bookmark2 = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id
        )
        
        # Create bookmark for another user (should remain)
        bookmark3 = Bookmark.objects.create(
            user=self.user2,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        # Verify bookmarks exist
        self.assertEqual(Bookmark.objects.filter(user=self.user1).count(), 2)
        self.assertEqual(Bookmark.objects.filter(user=self.user2).count(), 1)
        
        # Delete user1
        user1_id = self.user1.id
        self.user1.delete()
        
        # Verify user1's bookmarks are removed
        self.assertEqual(Bookmark.objects.filter(user_id=user1_id).count(), 0)
        
        # Verify user2's bookmark remains
        self.assertEqual(Bookmark.objects.filter(user=self.user2).count(), 1)
    
    def test_no_error_when_no_bookmarks_exist(self):
        """Test that deleting content with no bookmarks doesn't cause errors."""
        # Verify no bookmarks exist for the video
        self.assertEqual(Bookmark.objects.filter(object_id=self.video.id).count(), 0)
        
        # Delete the video - should not raise any errors
        try:
            self.video.delete()
        except Exception as e:
            self.fail(f"Deleting content with no bookmarks raised exception: {e}")
    
    def test_bookmark_signal_preserves_unrelated_bookmarks(self):
        """Test that deleting one object doesn't affect bookmarks for other objects."""
        # Create bookmarks for different objects
        video_bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        article_bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id
        )
        recipe_bookmark = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Recipe),
            object_id=self.recipe.id
        )
        
        # Delete only the video
        self.video.delete()
        
        # Verify only video bookmark is removed
        self.assertFalse(Bookmark.objects.filter(id=video_bookmark.id).exists())
        self.assertTrue(Bookmark.objects.filter(id=article_bookmark.id).exists())
        self.assertTrue(Bookmark.objects.filter(id=recipe_bookmark.id).exists())


class BookmarkDataIntegrityTests(BaseTestCase):
    """Test bookmark data integrity and edge cases."""
    
    def setUp(self):
        """Set up test data."""
        self.user = self.create_user(email="testuser@example.com")
        self.video = self.factory.create_video(title="Test Video")
    
    def test_orphaned_bookmark_detection(self):
        """Test that orphaned bookmarks can be detected."""
        # Create a bookmark
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        # Manually delete the video without triggering signals
        Video.objects.filter(id=self.video.id).delete()
        
        # Refresh bookmark from database
        bookmark.refresh_from_db()
        
        # content_object should now be None (orphaned)
        self.assertIsNone(bookmark.content_object)
        
        # get_content_representation should handle this gracefully
        representation = bookmark.get_content_representation()
        self.assertIsNone(representation)
    
    def test_bookmark_model_handles_missing_content(self):
        """Test that bookmark model methods handle missing content gracefully."""
        # Create a bookmark
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=99999  # Non-existent ID
        )
        
        # content_object should be None
        self.assertIsNone(bookmark.content_object)
        
        # String representation should still work
        str_repr = str(bookmark)
        self.assertIn(self.user.username, str_repr)
        self.assertIn("None", str_repr)
        
        # get_content_representation should return None
        representation = bookmark.get_content_representation()
        self.assertIsNone(representation)