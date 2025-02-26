from django.db import models
from django.utils.text import slugify
from PIL import Image
from io import BytesIO
from django.core.files import File
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
from django.utils import timezone
import logging
import os

from learning_resources.constants import DAYS_TO_CACHE_THUMBNAIL


class Video(models.Model):
    CATEGORY_CHOICES = [
        ('general', 'General'),
        ('devotional', 'Devotional'),
        ('tutorial', 'Tutorial'),
        ('morning_prayers', 'Morning Prayers'),
        ('evening_prayers', 'Evening Prayers'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='general',
        db_index=True
    )
    thumbnail = models.ImageField(
        upload_to='videos/thumbnails/',
        help_text='Recommended size: 720x1280 pixels (portrait)'
    )
    video = models.FileField(
        upload_to='videos/',
        help_text='Supported formats: MP4, WebM. Portrait orientation (9:16)'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    thumbnail_small = ImageSpecField(
        source='thumbnail',
        processors=[ResizeToFill(169, 300)],  # 9:16 aspect ratio (portrait)
        format='JPEG',
        options={'quality': 85}
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)

    def save(self, **kwargs):
        # First save the model to ensure the image is properly saved and uploaded to S3
        is_new_image = 'thumbnail' in kwargs.get('update_fields', []) if kwargs.get('update_fields') else self._state.adding
        super().save(**kwargs)
        
        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.thumbnail:
            # Update cache if:
            # 1. No cached URL exists
            # 2. New image was uploaded
            # 3. Cache is older than 7 days
            should_update_cache = (
                not self.cached_thumbnail_url or
                is_new_image or
                (self.cached_thumbnail_updated and 
                 (timezone.now() - self.cached_thumbnail_updated).days >= DAYS_TO_CACHE_THUMBNAIL)
            )
            
            if should_update_cache:
                try:
                    # Force generation of the thumbnail and wait for S3 upload
                    thumbnail = self.thumbnail_small.generate()
                    
                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.thumbnail_small.url
                    self.cached_thumbnail_updated = timezone.now()
                    
                    # Save again to update the cache fields only
                    super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
                except Exception as e:
                    logging.error(f"Error caching S3 thumbnail URL for Video {self.id}: {e}")
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Videos'

class Article(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField(
        help_text='Content in Markdown format'
    )
    image = models.ImageField(
        upload_to='articles/images/',
        help_text='Main article image. Recommended size: 1600x1200 pixels (4:3)'
    )
    thumbnail = ImageSpecField(
        source='image',
        processors=[ResizeToFill(400, 300)],  # 4:3 aspect ratio
        format='JPEG',
        options={'quality': 85}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)

    def save(self, **kwargs):
        # First save the model to ensure the image is properly saved and uploaded to S3
        is_new_image = 'image' in kwargs.get('update_fields', []) if kwargs.get('update_fields') else self._state.adding
        super().save(**kwargs)
        
        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.image:
            # Update cache if:
            # 1. No cached URL exists
            # 2. New image was uploaded
            # 3. Cache is older than 7 days
            should_update_cache = (
                not self.cached_thumbnail_url or
                is_new_image or
                (self.cached_thumbnail_updated and 
                 (timezone.now() - self.cached_thumbnail_updated).days >= DAYS_TO_CACHE_THUMBNAIL)
            )
            
            if should_update_cache:
                try:
                    # Force generation of the thumbnail and wait for S3 upload
                    thumbnail = self.thumbnail.generate()
                    
                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.thumbnail.url
                    self.cached_thumbnail_updated = timezone.now()
                    
                    # Save again to update the cache fields only
                    super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
                except Exception as e:
                    logging.error(f"Error caching S3 thumbnail URL for Article {self.id}: {e}")
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Articles'
