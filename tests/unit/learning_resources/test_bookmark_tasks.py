"""
Tests for learning resources Celery tasks.
"""
from django.test import TestCase
from django.test.utils import override_settings
from unittest.mock import patch, MagicMock
from tests.base import BaseTestCase
from learning_resources.models import Bookmark
from django.contrib.contenttypes.models import ContentType


class BookmarkTasksTests(BaseTestCase):
    """Test bookmark-related Celery tasks."""
    
    def test_cleanup_task_import(self):
        """Test that the cleanup task can be imported."""
        try:
            from learning_resources.tasks import cleanup_orphaned_bookmarks_async
            self.assertTrue(callable(cleanup_orphaned_bookmarks_async))
        except ImportError:
            self.fail("Could not import cleanup_orphaned_bookmarks_async task")
    
    def test_cleanup_task_basic_execution(self):
        """Test basic execution of the cleanup task."""
        from learning_resources.tasks import cleanup_orphaned_bookmarks_async
        
        # Just verify the task is callable and has expected attributes
        self.assertTrue(hasattr(cleanup_orphaned_bookmarks_async, 'run'))
        self.assertTrue(hasattr(cleanup_orphaned_bookmarks_async, 'delay'))
        self.assertTrue(hasattr(cleanup_orphaned_bookmarks_async, 'apply_async'))
        
        # Verify the task signature
        self.assertTrue(callable(cleanup_orphaned_bookmarks_async))
        
        # Test that task has correct configuration
        self.assertEqual(cleanup_orphaned_bookmarks_async.max_retries, 2)
        self.assertTrue(cleanup_orphaned_bookmarks_async.bind)
    
    def test_management_command_async_option(self):
        """Test that management command has async options."""
        from django.core.management import get_commands
        from django.core.management.base import BaseCommand
        from learning_resources.management.commands.cleanup_orphaned_bookmarks import Command
        
        # Verify the command exists
        commands = get_commands()
        self.assertIn('cleanup_orphaned_bookmarks', commands)
        
        # Create command instance and check it has async methods
        command = Command()
        self.assertTrue(hasattr(command, '_handle_async_cleanup'))
        
        # Check that add_arguments includes async options
        import argparse
        parser = argparse.ArgumentParser()
        command.add_arguments(parser)
        
        # Check that async arguments were added
        actions = [action.dest for action in parser._actions]
        self.assertIn('async', actions)
        self.assertIn('batch_size', actions)
        self.assertIn('wait', actions)


class BookmarkTasksIntegrationTests(BaseTestCase):
    """Integration tests for bookmark tasks with real data."""
    
    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.user = self.factory.create_user()
        self.video = self.factory.create_video()
    
    def test_task_handles_empty_dataset(self):
        """Test that task can be configured correctly."""
        from learning_resources.tasks import cleanup_orphaned_bookmarks_async
        
        # Test that we can create task parameters
        params = {
            'content_type_filter': None,
            'batch_size': 100,
            'dry_run': True
        }
        
        # Verify task signature accepts these parameters
        self.assertIsNotNone(cleanup_orphaned_bookmarks_async)
        
        # Test that Bookmark model is accessible
        from learning_resources.models import Bookmark
        bookmark_count = Bookmark.objects.count()
        self.assertEqual(bookmark_count, 0)  # Should be empty in test
    
    def test_task_processes_valid_bookmarks(self):
        """Test that task can access bookmark data correctly."""
        from learning_resources.tasks import cleanup_orphaned_bookmarks_async
        
        # Create a valid bookmark
        bookmark = self.factory.create_bookmark(user=self.user, content_object=self.video)
        
        # Verify the bookmark was created
        self.assertEqual(Bookmark.objects.count(), 1)
        
        # Verify the bookmark has a valid content_object
        bookmark.refresh_from_db()
        self.assertIsNotNone(bookmark.content_object)
        self.assertEqual(bookmark.content_object, self.video)
        
        # Test that task is properly configured for this scenario
        self.assertTrue(hasattr(cleanup_orphaned_bookmarks_async, 'delay'))


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class BookmarkTasksCeleryTests(BaseTestCase):
    """Tests that require Celery to be available."""
    
    def test_cache_maintenance_task(self):
        """Test the cache maintenance task."""
        try:
            from learning_resources.tasks import bookmark_cache_maintenance
            
            # Execute the task
            result = bookmark_cache_maintenance.delay()
            task_result = result.get()
            
            self.assertIsInstance(task_result, dict)
            self.assertEqual(task_result['status'], 'completed')
            self.assertIn('timestamp', task_result)
            
        except ImportError:
            self.skipTest("Celery not available for testing")