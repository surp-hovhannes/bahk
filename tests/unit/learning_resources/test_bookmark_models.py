"""Unit tests for bookmark models."""

from django.test import TestCase
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.core.exceptions import ValidationError
from learning_resources.models import Video, Article, Recipe, Bookmark
from hub.models import DevotionalSet
from tests.base import BaseTestCase


class BookmarkModelTests(BaseTestCase):
    """Test cases for the Bookmark model."""
    
    def setUp(self):
        """Set up test data."""
        self.user = self.create_user(email="testuser@example.com")
        self.other_user = self.create_user(email="otheruser@example.com")
        self.video = self.factory.create_video(title="Test Video")
        self.article = self.factory.create_article(title="Test Article")
        self.recipe = self.factory.create_recipe(title="Test Recipe")
        self.devotional_set = self.factory.create_devotional_set(title="Test Devotional Set")
    
    def test_bookmark_creation_with_video(self):
        """Test creating a bookmark for a video."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note="Great video!"
        )
        
        self.assertEqual(bookmark.user, self.user)
        self.assertEqual(bookmark.content_object, self.video)
        self.assertEqual(bookmark.note, "Great video!")
        self.assertEqual(bookmark.content_type_name, "video")
        self.assertIsNotNone(bookmark.created_at)
    
    def test_bookmark_creation_with_article(self):
        """Test creating a bookmark for an article."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id,
            note="Helpful article"
        )
        
        self.assertEqual(bookmark.content_object, self.article)
        self.assertEqual(bookmark.content_type_name, "article")
    
    def test_bookmark_creation_with_recipe(self):
        """Test creating a bookmark for a recipe."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Recipe),
            object_id=self.recipe.id
        )
        
        self.assertEqual(bookmark.content_object, self.recipe)
        self.assertEqual(bookmark.content_type_name, "recipe")
    
    def test_bookmark_creation_with_devotional_set(self):
        """Test creating a bookmark for a devotional set."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(DevotionalSet),
            object_id=self.devotional_set.id
        )
        
        self.assertEqual(bookmark.content_object, self.devotional_set)
        self.assertEqual(bookmark.content_type_name, "devotionalset")
    
    def test_bookmark_string_representation(self):
        """Test bookmark string representation."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        expected = f"{self.user.username} -> {self.video}"
        self.assertEqual(str(bookmark), expected)
    
    def test_bookmark_unique_constraint(self):
        """Test that a user cannot bookmark the same item twice."""
        # Create first bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        # Try to create duplicate bookmark
        with self.assertRaises(IntegrityError):
            Bookmark.objects.create(
                user=self.user,
                content_type=ContentType.objects.get_for_model(Video),
                object_id=self.video.id
            )
    
    def test_different_users_can_bookmark_same_item(self):
        """Test that different users can bookmark the same item."""
        # User 1 bookmarks video
        bookmark1 = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note="User 1 note"
        )
        
        # User 2 bookmarks same video
        bookmark2 = Bookmark.objects.create(
            user=self.other_user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note="User 2 note"
        )
        
        self.assertNotEqual(bookmark1, bookmark2)
        self.assertEqual(bookmark1.content_object, bookmark2.content_object)
        self.assertNotEqual(bookmark1.user, bookmark2.user)
    
    def test_bookmark_note_optional(self):
        """Test that bookmark note is optional."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        self.assertIsNone(bookmark.note)
    
    def test_bookmark_note_can_be_blank(self):
        """Test that bookmark note can be blank."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note=""
        )
        
        self.assertEqual(bookmark.note, "")
    
    def test_get_content_representation_video(self):
        """Test get_content_representation for video bookmark."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        representation = bookmark.get_content_representation()
        
        self.assertEqual(representation['id'], self.video.id)
        self.assertEqual(representation['type'], 'video')
        self.assertEqual(representation['title'], self.video.title)
        self.assertEqual(representation['description'], self.video.description)
        self.assertIn('created_at', representation)
    
    def test_get_content_representation_article(self):
        """Test get_content_representation for article bookmark."""
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id
        )
        
        representation = bookmark.get_content_representation()
        
        self.assertEqual(representation['id'], self.article.id)
        self.assertEqual(representation['type'], 'article')
        self.assertEqual(representation['title'], self.article.title)
        self.assertIn('created_at', representation)
    
    def test_get_content_representation_nonexistent_object(self):
        """Test get_content_representation when content object is deleted."""
        from django.test.utils import override_settings
        from django.db.models.signals import post_delete
        from learning_resources.signals import video_deleted_signal
        
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        # Temporarily disconnect the signal to create an orphaned bookmark
        post_delete.disconnect(video_deleted_signal, sender=Video)
        
        try:
            # Delete the video without triggering bookmark cleanup
            self.video.delete()
            
            # Refresh bookmark from database
            bookmark.refresh_from_db()
            
            # Should return None for deleted object
            representation = bookmark.get_content_representation()
            self.assertIsNone(representation)
        finally:
            # Reconnect the signal
            post_delete.connect(video_deleted_signal, sender=Video)
    
    def test_bookmark_ordering(self):
        """Test that bookmarks are ordered by creation date (newest first)."""
        import time
        
        # Create bookmarks with slight time differences
        bookmark1 = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        time.sleep(0.01)  # Small delay
        
        bookmark2 = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id
        )
        
        bookmarks = list(Bookmark.objects.all())
        
        # Should be ordered newest first
        self.assertEqual(bookmarks[0], bookmark2)
        self.assertEqual(bookmarks[1], bookmark1)
    
    def test_bookmark_meta_verbose_names(self):
        """Test bookmark model meta verbose names."""
        self.assertEqual(Bookmark._meta.verbose_name, 'Bookmark')
        self.assertEqual(Bookmark._meta.verbose_name_plural, 'Bookmarks')
    
    def test_bookmark_indexes(self):
        """Test that proper database indexes exist."""
        # Check that we have the expected indexes defined
        expected_index_fields = [
            ['user', 'content_type'],
            ['user', 'created_at']
        ]
        
        actual_index_fields = [list(index.fields) for index in Bookmark._meta.indexes]
        
        # Check that our expected indexes are present
        for expected_fields in expected_index_fields:
            self.assertIn(expected_fields, actual_index_fields)


class BookmarkQueryTests(BaseTestCase):
    """Test bookmark queryset and manager methods."""
    
    def setUp(self):
        """Set up test data."""
        self.user1 = self.create_user(email="user1@example.com")
        self.user2 = self.create_user(email="user2@example.com")
        
        # Create content
        self.video1 = self.factory.create_video(title="Video 1")
        self.video2 = self.factory.create_video(title="Video 2")
        self.article1 = self.factory.create_article(title="Article 1")
        
        # Create bookmarks
        self.bookmark1 = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video1.id
        )
        self.bookmark2 = Bookmark.objects.create(
            user=self.user1,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article1.id
        )
        self.bookmark3 = Bookmark.objects.create(
            user=self.user2,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video2.id
        )
    
    def test_filter_by_user(self):
        """Test filtering bookmarks by user."""
        user1_bookmarks = Bookmark.objects.filter(user=self.user1)
        user2_bookmarks = Bookmark.objects.filter(user=self.user2)
        
        self.assertEqual(user1_bookmarks.count(), 2)
        self.assertEqual(user2_bookmarks.count(), 1)
        
        self.assertIn(self.bookmark1, user1_bookmarks)
        self.assertIn(self.bookmark2, user1_bookmarks)
        self.assertIn(self.bookmark3, user2_bookmarks)
    
    def test_filter_by_content_type(self):
        """Test filtering bookmarks by content type."""
        video_ct = ContentType.objects.get_for_model(Video)
        article_ct = ContentType.objects.get_for_model(Article)
        
        video_bookmarks = Bookmark.objects.filter(content_type=video_ct)
        article_bookmarks = Bookmark.objects.filter(content_type=article_ct)
        
        self.assertEqual(video_bookmarks.count(), 2)
        self.assertEqual(article_bookmarks.count(), 1)
    
    def test_filter_by_user_and_content_type(self):
        """Test filtering bookmarks by user and content type."""
        video_ct = ContentType.objects.get_for_model(Video)
        
        user1_video_bookmarks = Bookmark.objects.filter(
            user=self.user1,
            content_type=video_ct
        )
        
        self.assertEqual(user1_video_bookmarks.count(), 1)
        self.assertEqual(user1_video_bookmarks.first(), self.bookmark1)
    
    def test_bookmark_exists(self):
        """Test checking if a bookmark exists."""
        video_ct = ContentType.objects.get_for_model(Video)
        
        # Existing bookmark
        exists = Bookmark.objects.filter(
            user=self.user1,
            content_type=video_ct,
            object_id=self.video1.id
        ).exists()
        self.assertTrue(exists)
        
        # Non-existing bookmark
        not_exists = Bookmark.objects.filter(
            user=self.user2,
            content_type=video_ct,
            object_id=self.video1.id
        ).exists()
        self.assertFalse(not_exists)