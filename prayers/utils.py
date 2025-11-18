"""Utility functions for the prayers app."""
import os


def prayer_set_image_upload_path(instance, filename):
    """Generate upload path for prayer set images."""
    ext = os.path.splitext(filename)[1]
    return f'prayer_sets/{instance.id}/image{ext}'


def prayer_request_image_upload_path(instance, filename):
    """Generate upload path for prayer request images."""
    ext = os.path.splitext(filename)[1]
    return f'prayer_requests/{instance.id}/image{ext}'

