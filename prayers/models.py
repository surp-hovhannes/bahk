"""Models for the prayers app."""
import logging

from django.db import models
from django.utils import timezone
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
from model_utils.tracker import FieldTracker
from modeltrans.fields import TranslationField
from taggit.managers import TaggableManager

from hub.constants import DAYS_TO_CACHE_THUMBNAIL
from hub.models import Church, Fast
from prayers.utils import prayer_set_image_upload_path

logger = logging.getLogger(__name__)


class Prayer(models.Model):
    """Model for a prayer."""
    
    CATEGORY_CHOICES = [
        ('morning', 'Morning Prayer'),
        ('evening', 'Evening Prayer'),
        ('meal', 'Meal Prayer'),
        ('general', 'General Prayer'),
        ('liturgical', 'Liturgical Prayer'),
        ('penitential', 'Penitential Prayer'),
        ('thanksgiving', 'Thanksgiving Prayer'),
        ('intercession', 'Intercession Prayer')
    ]
    
    title = models.CharField(max_length=200)
    text = models.TextField(help_text='Main prayer content')
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='general',
        db_index=True,
        help_text='Category of prayer'
    )
    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='prayers',
        help_text='Church this prayer belongs to'
    )
    fast = models.ForeignKey(
        Fast,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='prayers',
        help_text='Optional fast association'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Tags using django-taggit
    tags = TaggableManager(blank=True, help_text='Tags for categorizing prayers')
    
    # Translations for user-facing fields
    i18n = TranslationField(fields=(
        'title',
        'text',
    ))
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Prayer'
        verbose_name_plural = 'Prayers'
        indexes = [
            models.Index(fields=['church', 'category']),
            models.Index(fields=['church', 'fast']),
        ]
    
    def __str__(self):
        return self.title


class PrayerSet(models.Model):
    """Model for an ordered collection of prayers."""
    
    title = models.CharField(max_length=128)
    description = models.TextField(
        null=True,
        blank=True,
        help_text='Description of the prayer set'
    )
    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='prayer_sets',
        help_text='Church this prayer set belongs to'
    )
    image = models.ImageField(
        upload_to=prayer_set_image_upload_path,
        null=True,
        blank=True,
        help_text='Image for the prayer set. Recommended size: 1600x1200 pixels (4:3)'
    )
    thumbnail = ImageSpecField(
        source='image',
        processors=[ResizeToFill(400, 300)],  # 4:3 aspect ratio
        format='JPEG',
        options={'quality': 85}
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    
    prayers = models.ManyToManyField(
        'Prayer',
        through='PrayerSetMembership',
        related_name='prayer_sets',
        help_text='Prayers in this set'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Translations for user-facing fields
    i18n = TranslationField(fields=(
        'title',
        'description',
    ))
    
    # Track changes to the image field
    tracker = FieldTracker(fields=['image'])
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Prayer Set'
        verbose_name_plural = 'Prayer Sets'
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
                        f'Error caching S3 thumbnail URL for PrayerSet {self.id}: {e}'
                    )
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(
                    update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated']
                )


class PrayerSetMembership(models.Model):
    """Through model for ordering prayers within a prayer set."""
    
    prayer_set = models.ForeignKey(
        PrayerSet,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    prayer = models.ForeignKey(
        Prayer,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text='Order of prayer within the set'
    )
    
    class Meta:
        ordering = ['order']
        unique_together = ('prayer_set', 'prayer')
        verbose_name = 'Prayer Set Membership'
        verbose_name_plural = 'Prayer Set Memberships'
    
    def __str__(self):
        return f'{self.prayer.title} in {self.prayer_set.title} (order: {self.order})'

