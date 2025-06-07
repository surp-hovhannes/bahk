"""Test utilities for cleanup and setup."""
import os
import shutil
from django.conf import settings


def cleanup_test_media():
    """Remove all test media files but keep the directories."""
    if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
        test_media_root = settings.MEDIA_ROOT
        if os.path.exists(test_media_root) and 'test_media' in test_media_root:
            # Only clean if it's clearly a test directory
            try:
                # Instead of removing the entire directory, remove its contents
                for root, dirs, files in os.walk(test_media_root, topdown=False):
                    # Remove all files
                    for file in files:
                        file_path = os.path.join(root, file)
                        os.remove(file_path)
                    # Remove all subdirectories (but not the root test_media directory)
                    for dir in dirs:
                        dir_path = os.path.join(root, dir)
                        if dir_path != test_media_root:  # Don't remove the root directory
                            os.rmdir(dir_path)
                print(f"Cleaned up test media contents: {test_media_root}")
            except Exception as e:
                print(f"Warning: Could not clean up test media contents {test_media_root}: {e}")


def setup_test_media():
    """Ensure test media directory exists."""
    if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True) 