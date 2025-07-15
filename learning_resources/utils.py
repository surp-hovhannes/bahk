import os
import uuid
from django.utils import timezone
from django.utils.text import slugify

def generate_unique_filename(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    base_name = os.path.splitext(filename)[0]
    slug_name = slugify(base_name)
    unique_id = str(uuid.uuid4())[:8]
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    return f"{timestamp}_{unique_id}_{slug_name}{ext}"

def video_upload_path(instance, filename):
    return f"videos/{generate_unique_filename(instance, filename)}"

def video_thumbnail_upload_path(instance, filename):
    return f"videos/thumbnails/{generate_unique_filename(instance, filename)}"

def article_image_upload_path(instance, filename):
    return f"articles/images/{generate_unique_filename(instance, filename)}"

def recipe_image_upload_path(instance, filename):
    return f"recipes/images/{generate_unique_filename(instance, filename)}" 