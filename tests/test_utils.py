"""Test utilities for cleanup and setup."""
import os
from pathlib import Path
from django.conf import settings


def _is_allowed_test_media_root(test_media_root):
    """Return true only for a real, non-symlink test_media directory."""
    candidate = Path(test_media_root)
    if candidate.is_symlink():
        print(f"Refusing to clean symlinked test media directory: {test_media_root}")
        return False

    try:
        resolved_candidate = candidate.resolve(strict=True)
    except FileNotFoundError:
        return False

    if "test_media" not in str(candidate) or resolved_candidate != candidate.absolute():
        print(f"Refusing to clean unexpected test media path: {test_media_root}")
        return False

    return candidate.is_dir()


def cleanup_test_media():
    """Remove all test media files but keep the directories."""
    if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
        test_media_root = settings.MEDIA_ROOT
        if os.path.exists(test_media_root) and _is_allowed_test_media_root(test_media_root):
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
                return True
            except Exception as e:
                print(f"Warning: Could not clean up test media contents {test_media_root}: {e}")
    return False


def setup_test_media():
    """Ensure test media directory exists."""
    if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True) 
