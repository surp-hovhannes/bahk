"""Integration tests for bookmark API endpoints."""

import json
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.test import APITestCase
from learning_resources.models import Video, Article, Recipe, Bookmark
from hub.models import DevotionalSet
from tests.base import BaseAPITestCase


class BookmarkAPITests(BaseAPITestCase):
    """Test cases for bookmark API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        self.user = self.create_user(email="testuser@example.com")
        self.other_user = self.create_user(email="otheruser@example.com")
        
        # Create content to bookmark
        self.video = self.factory.create_video(title="Test Video")
        self.article = self.factory.create_article(title="Test Article")
        self.recipe = self.factory.create_recipe(title="Test Recipe")
        self.devotional_set = self.factory.create_devotional_set(title="Test Devotional Set")
        
        # Authenticate the main user
        self.authenticate(self.user)
    
    def test_create_video_bookmark(self):
        """Test creating a bookmark for a video."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'video',
            'object_id': self.video.id,
            'note': 'Great video!'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Check response data
        self.assertEqual(response.data['content_type_name'], 'video')
        self.assertEqual(response.data['object_id'], self.video.id)
        self.assertEqual(response.data['note'], 'Great video!')
        self.assertIn('content', response.data)
        self.assertEqual(response.data['content']['title'], self.video.title)
        
        # Check database
        bookmark = Bookmark.objects.get(user=self.user, object_id=self.video.id)
        self.assertEqual(bookmark.note, 'Great video!')
    
    def test_create_article_bookmark(self):
        """Test creating a bookmark for an article."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'article',
            'object_id': self.article.id,
            'note': 'Helpful article'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content_type_name'], 'article')
    
    def test_create_recipe_bookmark(self):
        """Test creating a bookmark for a recipe."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'recipe',
            'object_id': self.recipe.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content_type_name'], 'recipe')
    
    def test_create_devotional_set_bookmark(self):
        """Test creating a bookmark for a devotional set."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'devotionalset',
            'object_id': self.devotional_set.id,
            'note': 'Inspiring devotionals'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content_type_name'], 'devotionalset')
    
    def test_create_bookmark_without_note(self):
        """Test creating a bookmark without a note."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'video',
            'object_id': self.video.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['note'])
    
    def test_create_duplicate_bookmark_fails(self):
        """Test that creating a duplicate bookmark fails."""
        # Create first bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        # Try to create duplicate
        url = reverse('bookmark-create')
        data = {
            'content_type': 'video',
            'object_id': self.video.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already bookmarked', str(response.data))
    
    def test_create_bookmark_invalid_content_type(self):
        """Test creating bookmark with invalid content type."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'invalid_type',
            'object_id': self.video.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not allowed for bookmarking', str(response.data))
    
    def test_create_bookmark_nonexistent_object(self):
        """Test creating bookmark for non-existent object."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'video',
            'object_id': 99999  # Non-existent ID
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('does not exist', str(response.data))
    
    def test_create_bookmark_requires_authentication(self):
        """Test that creating bookmarks requires authentication."""
        self.client.force_authenticate(user=None)  # Remove authentication
        
        url = reverse('bookmark-create')
        data = {
            'content_type': 'video',
            'object_id': self.video.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_list_user_bookmarks(self):
        """Test listing user's bookmarks."""
        # Create some bookmarks
        bookmark1 = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id,
            note="Video note"
        )
        bookmark2 = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id,
            note="Article note"
        )
        
        # Create bookmark for another user (should not be included)
        Bookmark.objects.create(
            user=self.other_user,
            content_type=ContentType.objects.get_for_model(Recipe),
            object_id=self.recipe.id
        )
        
        url = reverse('bookmark-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
        
        # Check content is included
        bookmark_data = response.data['results'][0]
        self.assertIn('content', bookmark_data)
        self.assertIn('title', bookmark_data['content'])
    
    def test_list_bookmarks_filter_by_content_type(self):
        """Test filtering bookmarks by content type."""
        # Create bookmarks of different types
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=self.article.id
        )
        
        # Filter by video content type
        url = reverse('bookmark-list')
        response = self.client.get(url, {'content_type': 'video'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['content_type_name'], 'video')
    
    def test_list_bookmarks_requires_authentication(self):
        """Test that listing bookmarks requires authentication."""
        self.client.force_authenticate(user=None)
        
        url = reverse('bookmark-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_delete_bookmark(self):
        """Test deleting a bookmark."""
        # Create bookmark
        bookmark = Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        url = reverse('bookmark-delete', kwargs={
            'content_type': 'video',
            'object_id': self.video.id
        })
        
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Check bookmark is deleted
        self.assertFalse(
            Bookmark.objects.filter(
                user=self.user,
                content_type=ContentType.objects.get_for_model(Video),
                object_id=self.video.id
            ).exists()
        )
    
    def test_delete_nonexistent_bookmark(self):
        """Test deleting a non-existent bookmark."""
        url = reverse('bookmark-delete', kwargs={
            'content_type': 'video',
            'object_id': self.video.id
        })
        
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_delete_other_users_bookmark_fails(self):
        """Test that users cannot delete other users' bookmarks."""
        # Create bookmark for other user
        Bookmark.objects.create(
            user=self.other_user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        url = reverse('bookmark-delete', kwargs={
            'content_type': 'video',
            'object_id': self.video.id
        })
        
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        
        # Bookmark should still exist
        self.assertTrue(
            Bookmark.objects.filter(
                user=self.other_user,
                content_type=ContentType.objects.get_for_model(Video),
                object_id=self.video.id
            ).exists()
        )
    
    def test_delete_bookmark_invalid_content_type(self):
        """Test deleting bookmark with invalid content type."""
        url = reverse('bookmark-delete', kwargs={
            'content_type': 'invalid_type',
            'object_id': self.video.id
        })
        
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid content type', str(response.data))
    
    def test_delete_bookmark_requires_authentication(self):
        """Test that deleting bookmarks requires authentication."""
        self.client.force_authenticate(user=None)
        
        url = reverse('bookmark-delete', kwargs={
            'content_type': 'video',
            'object_id': self.video.id
        })
        
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_check_bookmark_status(self):
        """Test checking if an item is bookmarked."""
        # Create bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video.id
        )
        
        # Check bookmarked item
        url = reverse('bookmark-check', kwargs={
            'content_type': 'video',
            'object_id': self.video.id
        })
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_bookmarked'])
        self.assertIn('bookmark_id', response.data)
        
        # Check non-bookmarked item
        url = reverse('bookmark-check', kwargs={
            'content_type': 'article',
            'object_id': self.article.id
        })
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_bookmarked'])
        self.assertNotIn('bookmark_id', response.data)
    
    def test_check_bookmark_requires_authentication(self):
        """Test that checking bookmark status requires authentication."""
        self.client.force_authenticate(user=None)
        
        url = reverse('bookmark-check', kwargs={
            'content_type': 'video',
            'object_id': self.video.id
        })
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class BookmarkIntegrationWithContentAPITests(BaseAPITestCase):
    """Test bookmark integration with content listing APIs."""
    
    def setUp(self):
        """Set up test data."""
        self.user = self.create_user(email="testuser@example.com")
        self.authenticate(self.user)
        
        # Create content
        self.video1 = self.factory.create_video(title="Video 1")
        self.video2 = self.factory.create_video(title="Video 2")
        
        # Bookmark only the first video
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Video),
            object_id=self.video1.id
        )
    
    def test_video_list_includes_bookmark_status(self):
        """Test that video list includes bookmark status for authenticated users."""
        url = reverse('video-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        videos = response.data['results']
        
        # Find our specific videos in the response
        video1_data = next((v for v in videos if v['id'] == self.video1.id), None)
        video2_data = next((v for v in videos if v['id'] == self.video2.id), None)
        
        self.assertIsNotNone(video1_data)
        self.assertIsNotNone(video2_data)
        
        # Check bookmark status
        self.assertTrue(video1_data['is_bookmarked'])
        self.assertFalse(video2_data['is_bookmarked'])
    
    def test_video_list_without_authentication(self):
        """Test that video list works without authentication (no bookmark status)."""
        self.client.force_authenticate(user=None)
        
        url = reverse('video-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Should still include is_bookmarked field, but always False
        videos = response.data['results']
        for video in videos:
            self.assertFalse(video['is_bookmarked'])
    
    def test_article_list_includes_bookmark_status(self):
        """Test that article list includes bookmark status."""
        # Create articles
        article1 = self.factory.create_article(title="Article 1")
        article2 = self.factory.create_article(title="Article 2")
        
        # Bookmark first article
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=article1.id
        )
        
        url = reverse('article-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        articles = response.data['results']
        
        # Find our specific articles
        article1_data = next((a for a in articles if a['id'] == article1.id), None)
        article2_data = next((a for a in articles if a['id'] == article2.id), None)
        
        self.assertIsNotNone(article1_data)
        self.assertIsNotNone(article2_data)
        
        # Check bookmark status
        self.assertTrue(article1_data['is_bookmarked'])
        self.assertFalse(article2_data['is_bookmarked'])
    
    def test_recipe_list_includes_bookmark_status(self):
        """Test that recipe list includes bookmark status."""
        # Create recipes
        recipe1 = self.factory.create_recipe(title="Recipe 1")
        recipe2 = self.factory.create_recipe(title="Recipe 2")
        
        # Bookmark first recipe
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Recipe),
            object_id=recipe1.id
        )
        
        url = reverse('recipe-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        recipes = response.data['results']
        
        # Find our specific recipes
        recipe1_data = next((r for r in recipes if r['id'] == recipe1.id), None)
        recipe2_data = next((r for r in recipes if r['id'] == recipe2.id), None)
        
        self.assertIsNotNone(recipe1_data)
        self.assertIsNotNone(recipe2_data)
        
        # Check bookmark status
        self.assertTrue(recipe1_data['is_bookmarked'])
        self.assertFalse(recipe2_data['is_bookmarked'])
    
    def test_devotional_set_list_includes_bookmark_status(self):
        """Test that devotional set list includes bookmark status."""
        # Create devotional sets
        devotional_set1 = self.factory.create_devotional_set(title="Devotional Set 1")
        devotional_set2 = self.factory.create_devotional_set(title="Devotional Set 2")
        
        # Bookmark first devotional set
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(DevotionalSet),
            object_id=devotional_set1.id
        )
        
        url = reverse('devotional-set-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        devotional_sets = response.data['results']
        
        # Find our specific devotional sets
        ds1_data = next((ds for ds in devotional_sets if ds['id'] == devotional_set1.id), None)
        ds2_data = next((ds for ds in devotional_sets if ds['id'] == devotional_set2.id), None)
        
        self.assertIsNotNone(ds1_data)
        self.assertIsNotNone(ds2_data)
        
        # Check bookmark status
        self.assertTrue(ds1_data['is_bookmarked'])
        self.assertFalse(ds2_data['is_bookmarked'])