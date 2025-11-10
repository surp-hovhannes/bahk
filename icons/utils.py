"""Utility functions for the icons app."""
import os
import uuid
from django.utils import timezone
from django.utils.text import slugify


def generate_unique_filename(instance, filename):
    """Generate a unique filename with timestamp and UUID."""
    ext = os.path.splitext(filename)[1].lower()
    base_name = os.path.splitext(filename)[0]
    slug_name = slugify(base_name)
    unique_id = str(uuid.uuid4())[:8]
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    return f"{timestamp}_{unique_id}_{slug_name}{ext}"


def icon_image_upload_path(instance, filename):
    """Generate upload path for icon images."""
    return f"icons/images/{generate_unique_filename(instance, filename)}"
