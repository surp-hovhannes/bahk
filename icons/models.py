"""Models for the icons app."""
import hashlib
import logging
from io import BytesIO

from django.db import models, transaction
from django.utils import timezone
import imagehash
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit
from model_utils.tracker import FieldTracker
from PIL import Image
from taggit.managers import TaggableManager

from hub.constants import DAYS_TO_CACHE_THUMBNAIL
from hub.models import Church
from icons.utils import icon_image_upload_path

logger = logging.getLogger(__name__)


class DuplicateIconError(Exception):
    """Raised when a new icon duplicates an existing icon."""

    def __init__(self, existing_icon):
        self.existing_icon = existing_icon
        super().__init__(f"Duplicate icon detected: {existing_icon.title}")


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
        help_text='Main icon image. Descriptive original filenames are preserved as metadata claims, not visual proof. Thumbnail will be resized to fit within 400x300 preserving aspect ratio.'
    )
    image_content_digest = models.CharField(max_length=64, blank=True, editable=False)
    image_revision = models.PositiveBigIntegerField(default=0, editable=False)
    original_filename = models.CharField(max_length=255, blank=True, editable=False)
    filename_provenance = models.CharField(max_length=32, default='unknown', editable=False)
    thumbnail = ImageSpecField(
        source='image',
        processors=[ResizeToFit(400, 300)],
        format='JPEG',
        options={'quality': 85}
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    image_hash = models.CharField(max_length=64, blank=True)
    phash = models.CharField(max_length=64, blank=True)
    
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
        constraints = [
            models.UniqueConstraint(
                fields=['image_hash'],
                name='unique_icon_image_hash',
                condition=models.Q(image_hash__gt=''),
                violation_error_message='An icon with this image hash already exists.',
            ),
        ]
    
    def __str__(self):
        return self.title

    def _compute_image_footprints(self):
        """Compute exact and perceptual hashes for the stored image."""
        self.image.open('rb')
        try:
            raw_bytes = self.image.read()
        finally:
            self.image.close()

        image_hash = hashlib.sha256(raw_bytes).hexdigest()
        phash = ''
        try:
            with Image.open(BytesIO(raw_bytes)) as image:
                phash = str(imagehash.phash(image))
        except Exception as exc:
            logger.warning('Could not compute pHash for Icon %s: %s', self.pk, exc)

        return image_hash, phash
    
    @transaction.atomic
    def save(self, *args, **kwargs):
        from icons.services.ingestion import schedule
        from icons.services.taxonomy_inputs import filename

        update_fields = args[3] if len(args) >= 4 else kwargs.get('update_fields')
        persists_image = update_fields is None or 'image' in update_fields
        if not self._state.adding:
            # Serialize supported image replacement and assignment on the icon row.
            previous = type(self).objects.select_for_update().get(pk=self.pk)
            self.image_revision = previous.image_revision
            self.image_content_digest = previous.image_content_digest
        if persists_image and self.image and not self.image._committed:
            self.original_filename = filename(self.image.name)
            self.filename_provenance = 'uploaded'
            if update_fields is not None:
                update_fields = set(update_fields) | {'original_filename', 'filename_provenance'}
                if len(args) >= 4:
                    args = (*args[:3], update_fields, *args[4:])
                else:
                    kwargs['update_fields'] = update_fields
        self._taxonomy_computed_digest = None
        self._save_with_footprints(*args, **kwargs)
        if persists_image:
            new_digest = self._taxonomy_computed_digest
            if not self.image:
                new_digest = ''
            if new_digest is not None and new_digest != self.image_content_digest:
                self.image_content_digest = new_digest
                self.image_revision += 1
                type(self).objects.filter(pk=self.pk).update(
                    image_content_digest=new_digest, image_revision=self.image_revision)
        schedule(self.pk)

    def _save_with_footprints(self, *args, **kwargs):
        """Save method with image footprint and thumbnail caching logic."""
        update_fields = kwargs.get('update_fields')
        if len(args) >= 4:
            update_fields = args[3]

        # First check if this is a new instance or if the image field has changed
        is_new_image = (update_fields is None or 'image' in update_fields) and (
            self._state.adding
            or 'image' in (update_fields or [])
            or (not self._state.adding and self.tracker.has_changed('image'))
        )
        
        super().save(*args, **kwargs)

        post_save_update_fields = []
        
        # Handle footprints and thumbnails after the instance and image are fully saved to S3
        if self.image:
            if is_new_image:
                try:
                    image_hash, phash = self._compute_image_footprints()
                    self._taxonomy_computed_digest = image_hash
                    if self.image_hash != image_hash:
                        duplicate_hash_exists = type(self).objects.exclude(
                            pk=self.pk
                        ).filter(image_hash=image_hash).exists()
                        if not duplicate_hash_exists:
                            self.image_hash = image_hash
                            post_save_update_fields.append('image_hash')
                        elif self.image_hash:
                            self.image_hash = ''
                            post_save_update_fields.append('image_hash')
                    if self.phash != phash:
                        self.phash = phash
                        post_save_update_fields.append('phash')
                except Exception as exc:
                    self._taxonomy_computed_digest = ''
                    logger.error(
                        'Error computing image footprints for Icon %s: %s',
                        self.pk,
                        exc,
                    )

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
                    post_save_update_fields.extend([
                        'cached_thumbnail_url',
                        'cached_thumbnail_updated',
                    ])
                except Exception as e:
                    logger.error(
                        f'Error caching S3 thumbnail URL for Icon {self.id}: {e}'
                    )
        else:
            # Clear cached URL and footprints if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                post_save_update_fields.extend([
                    'cached_thumbnail_url',
                    'cached_thumbnail_updated',
                ])
            if self.image_hash:
                self.image_hash = ''
                post_save_update_fields.append('image_hash')
            if self.phash:
                self.phash = ''
                post_save_update_fields.append('phash')

        if post_save_update_fields:
            super().save(update_fields=list(dict.fromkeys(post_save_update_fields)))


class IconFeedback(models.Model):
    """Feedback / correction submissions for icons."""

    class FeedbackType(models.TextChoices):
        MISLABEL = 'mislabel', 'Mislabeled'
        SUGGESTED_TAGS = 'suggested_tags', 'Suggest Tags'
        GENERAL = 'general', 'General'

    icon = models.ForeignKey(
        Icon, on_delete=models.CASCADE, related_name='feedback'
    )
    feedback_type = models.CharField(
        max_length=20, choices=FeedbackType.choices
    )
    description = models.TextField(help_text='The actual feedback text')
    suggested_tags = models.CharField(
        max_length=500, blank=True,
        help_text='Comma-separated tags, shown when type is suggested_tags'
    )
    submitter_email = models.EmailField(
        blank=True, help_text='Optional email for follow-up'
    )
    # Snapshots at submission time — immutable once created
    icon_title_at_time = models.CharField(
        max_length=200, blank=True,
        help_text='Snapshot of icon title at submission time'
    )
    icon_tags_at_time = models.TextField(
        blank=True,
        help_text='Snapshot of icon tags (comma-separated) at submission time'
    )
    # Moderation / resolution
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, help_text='Internal admin notes')
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    http_user_agent = models.TextField(blank=True, default='')
    ip_address = models.GenericIPAddressField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Icon Feedback'
        verbose_name_plural = 'Icon Feedback'
        indexes = [
            models.Index(fields=['icon', 'created_at']),
        ]

    def __str__(self):
        return f"Feedback #{self.pk} on {self.icon} ({self.feedback_type})"

# Django discovers these additive models through this module.
from icons.taxonomy_models import (  # noqa: E402,F401
    IconAnalysis, IconAssertion, IconObservation, IconTaxonomyProjection,
    IconTaxonomyWork, TaxonomyAlias, TaxonomyBudget, TaxonomyCall,
    TaxonomyConcept, TaxonomyRelation, TaxonomyRelease, TaxonomyRequestInterpretation,
)
