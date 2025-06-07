#!/usr/bin/env python
"""Standalone script to clean up test media files."""
import os
import shutil
import sys


def cleanup_test_media():
    """Remove test media files but keep the directories."""
    test_media_dirs = [
        'test_media',
        './test_media',
        os.path.join(os.getcwd(), 'test_media')
    ]
    
    cleaned = False
    for media_dir in test_media_dirs:
        if os.path.exists(media_dir) and 'test_media' in media_dir:
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