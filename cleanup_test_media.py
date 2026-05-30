#!/usr/bin/env python
"""Standalone script to clean up test media files."""
import os
from pathlib import Path


def _is_allowed_test_media_dir(media_dir):
    """Return true only for the real project-local test_media directory."""
    candidate = Path(media_dir)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    expected_dir = Path.cwd().resolve() / 'test_media'
    if candidate.is_symlink():
        print(f"⚠️  Refusing to clean symlinked test media directory: {media_dir}")
        return False

    try:
        resolved_candidate = candidate.resolve(strict=True)
    except FileNotFoundError:
        return False

    if resolved_candidate != expected_dir:
        print(f"⚠️  Refusing to clean unexpected test media path: {media_dir}")
        return False

    return candidate.is_dir()


def cleanup_test_media():
    """Remove test media files but keep the directories."""
    test_media_dirs = [
        'test_media',
        './test_media',
        os.path.join(os.getcwd(), 'test_media')
    ]
    
    cleaned = False
    for media_dir in test_media_dirs:
        if os.path.exists(media_dir) and _is_allowed_test_media_dir(media_dir):
            try:
                # Instead of removing the entire directory, remove its contents
                for root, dirs, files in os.walk(media_dir, topdown=False):
                    # Remove all files
                    for file in files:
                        file_path = os.path.join(root, file)
                        os.remove(file_path)
                    # Remove all subdirectories (but not the root test_media directory)
                    for dir in dirs:
                        dir_path = os.path.join(root, dir)
                        if dir_path != media_dir:  # Don't remove the root directory
                            os.rmdir(dir_path)
                print(f"✅ Cleaned up test media contents: {media_dir}")
                cleaned = True
            except Exception as e:
                print(f"❌ Warning: Could not clean up {media_dir}: {e}")
    
    if not cleaned:
        print("ℹ️  No test media directories found to clean up.")
    
    return cleaned


if __name__ == '__main__':
    print("🧹 Cleaning up test media files...")
    cleanup_test_media()
    print("✨ Done!") 
