"""Models for the icons app."""
import logging

from django.db import models
from django.utils import timezone
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit
from model_utils.tracker import FieldTracker
from taggit.managers import TaggableManager

from hub.constants import DAYS_TO_CACHE_THUMBNAIL
from hub.models import Church
from icons.utils import icon_image_upload_path

logger = logging.getLogger(__name__)


class Icon(models.Model):
    """Model for icons that can be used in the application."""
    
    title = models.CharField(max_length=200, help_text='Title of the icon')
    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='icons',
        help_text='Church this icon belongs to'
    )
    image = models.ImageField(
        upload_to=icon_image_upload_path,
        help_text='Main icon image. Thumbnail will be resized to fit within 400x300 preserving aspect ratio.'
    )
    thumbnail = ImageSpecField(
        source='image',
        processors=[ResizeToFit(400, 300)],
        format='JPEG',
        options={'quality': 85}
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Tags using django-taggit
    tags = TaggableManager(blank=True, help_text='Tags for categorizing icons')
    
    # Track changes to the image field
    tracker = FieldTracker(fields=['image'])
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Icon'
        verbose_name_plural = 'Icons'
        indexes = [
            models.Index(fields=['church', 'created_at']),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, **kwargs):
        """Save method with thumbnail caching logic."""
        # First check if this is a new instance or if the image field has changed
        is_new_image = (
            self._state.adding
            or 'image' in kwargs.get('update_fields', [])
            or (not self._state.adding and self.tracker.has_changed('image'))
        )
        
        super().save(**kwargs)
        
        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.image:
            # Update cache if:
            # 1. No cached URL exists
            # 2. Image was changed/uploaded
            # 3. Cache is older than 7 days
            should_update_cache = (
                not self.cached_thumbnail_url
                or is_new_image
                or (
                    self.cached_thumbnail_updated
                    and (timezone.now() - self.cached_thumbnail_updated).days
                    >= DAYS_TO_CACHE_THUMBNAIL
                )
            )
            
            if should_update_cache:
                try:
                    # Force generation of the thumbnail and wait for S3 upload
                    thumbnail = self.thumbnail.generate()
                    
                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.thumbnail.url
                    self.cached_thumbnail_updated = timezone.now()
                    
                    # Save again to update the cache fields only
                    super().save(
                        update_fields=[
                            'cached_thumbnail_url',
                            'cached_thumbnail_updated',
                        ]
                    )
                except Exception as e:
                    logger.error(
                        f'Error caching S3 thumbnail URL for Icon {self.id}: {e}'
                    )
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(
                    update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated']
                )
