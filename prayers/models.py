"""Models for the prayers app."""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
from model_utils.tracker import FieldTracker
from modeltrans.fields import TranslationField
from taggit.managers import TaggableManager

from hub.constants import DAYS_TO_CACHE_THUMBNAIL
from hub.models import Church, Fast
from learning_resources.models import Video
from prayers.utils import prayer_set_image_upload_path, prayer_request_image_upload_path

User = get_user_model()

logger = logging.getLogger(__name__)


class Prayer(models.Model):
    """Model for a prayer."""

    CATEGORY_CHOICES = [
        ('morning', 'Morning Prayer'),
        ('evening', 'Evening Prayer'),
        ('general', 'General Prayer')
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
    video = models.ForeignKey(
        Video,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='prayers',
        help_text='Video containing audio recording of the prayer being read aloud and visuals integrated with the prayer'
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

    CATEGORY_CHOICES = [
        ('morning', 'Morning Prayer'),
        ('evening', 'Evening Prayer'),
        ('general', 'General Prayer')
    ]

    title = models.CharField(max_length=128)
    description = models.TextField(
        null=True,
        blank=True,
        help_text='Description of the prayer set'
    )
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='general',
        db_index=True,
        help_text='Category of prayer set'
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

    # Track changes to fields requiring custom save behavior
    tracker = FieldTracker(fields=['image'])

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Prayer Set'
        verbose_name_plural = 'Prayer Sets'
        indexes = [
            models.Index(fields=['church', 'created_at']),
            models.Index(fields=['church', 'category']),
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


class PrayerRequestManager(models.Manager):
    """Custom manager for PrayerRequest model."""

    def get_active_approved(self):
        """Get all active, approved prayer requests that haven't expired."""
        return self.filter(
            status='approved',
            expiration_date__gt=timezone.now()
        ).exclude(
            status__in=['completed', 'deleted']
        )


class PrayerRequest(models.Model):
    """Model for user-submitted prayer requests."""

    STATUS_CHOICES = [
        ('pending_moderation', 'Pending Moderation'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('completed', 'Completed'),
        ('deleted', 'Deleted'),
    ]

    DURATION_CHOICES = [
        (1, '1 day'),
        (2, '2 days'),
        (3, '3 days'),
        (4, '4 days'),
        (5, '5 days'),
        (6, '6 days'),
        (7, '7 days'),
    ]

    MAX_ACTIVE_REQUESTS_PER_USER = 3

    title = models.CharField(
        max_length=200,
        help_text='Short title for the prayer request'
    )
    description = models.TextField(
        help_text='Detailed description of the prayer request'
    )
    is_anonymous = models.BooleanField(
        default=False,
        help_text='Hide requester identity from users (visible to admins)'
    )
    duration_days = models.PositiveSmallIntegerField(
        choices=DURATION_CHOICES,
        default=3,
        help_text='Duration of the prayer request (1-7 days)'
    )
    expiration_date = models.DateTimeField(
        help_text='Calculated expiration date based on creation + duration',
        db_index=True
    )
    image = models.ImageField(
        upload_to=prayer_request_image_upload_path,
        null=True,
        blank=True,
        help_text='Optional image for the prayer request. Recommended size: 1600x1200 pixels (4:3)'
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

    reviewed = models.BooleanField(
        default=False,
        help_text='Whether the request has been reviewed by moderation system'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending_moderation',
        db_index=True,
        help_text='Current status of the prayer request'
    )
    moderation_result = models.JSONField(
        null=True,
        blank=True,
        help_text='Results from LLM moderation process'
    )
    moderation_severity = models.CharField(
        max_length=20,
        choices=[
            ('low', 'Low'),
            ('medium', 'Medium'),
            ('high', 'High'),
            ('critical', 'Critical'),
        ],
        null=True,
        blank=True,
        db_index=True,
        help_text='Severity level from moderation'
    )
    requires_human_review = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Flagged for human review'
    )
    moderated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the request was moderated'
    )

    requester = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='prayer_requests',
        help_text='User who submitted the prayer request'
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Track changes to fields requiring custom save behavior
    tracker = FieldTracker(fields=['image', 'duration_days'])

    # Custom manager
    objects = PrayerRequestManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Prayer Request'
        verbose_name_plural = 'Prayer Requests'
        indexes = [
            models.Index(fields=['status', 'expiration_date']),
            models.Index(fields=['requester', 'status']),
            models.Index(fields=['reviewed', 'status']),
        ]

    def __str__(self):
        return f'{self.title} by {self.requester.get_full_name() or self.requester.email}'

    def clean(self):
        """Validate that user doesn't exceed max active requests."""
        if not self.pk:  # Only validate on creation
            active_count = PrayerRequest.objects.filter(
                requester=self.requester,
                status__in=['pending_moderation', 'approved']
            ).exclude(
                expiration_date__lt=timezone.now()
            ).count()

            if active_count >= self.MAX_ACTIVE_REQUESTS_PER_USER:
                raise ValidationError(
                    f'You cannot have more than {self.MAX_ACTIVE_REQUESTS_PER_USER} '
                    f'active prayer requests at once.'
                )

    def save(self, **kwargs):
        """Save method with expiration calculation and thumbnail caching."""
        # Calculate expiration date if not set or if duration changed
        duration_changed = (
            self._state.adding
            or 'duration_days' in kwargs.get('update_fields', [])
            or (not self._state.adding and self.tracker.has_changed('duration_days'))
        )
        if duration_changed:
            self.expiration_date = timezone.now() + timedelta(days=self.duration_days)

        # Check if this is a new instance or if the image field has changed
        is_new_image = (
            self._state.adding
            or 'image' in kwargs.get('update_fields', [])
            or (not self._state.adding and self.tracker.has_changed('image'))
        )

        super().save(**kwargs)

        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.image:
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
                        f'Error caching S3 thumbnail URL for PrayerRequest {self.id}: {e}'
                    )
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(
                    update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated']
                )

    def is_expired(self):
        """Check if the prayer request has expired."""
        return timezone.now() > self.expiration_date

    def mark_completed(self):
        """Mark the prayer request as completed."""
        if self.status != 'completed':
            self.status = 'completed'
            self.save(update_fields=['status', 'updated_at'])

    def get_acceptance_count(self):
        """Get the number of users who accepted this prayer request."""
        return self.acceptances.count()

    def get_prayer_log_count(self):
        """Get the total number of prayer logs for this request."""
        return self.prayer_logs.count()


class PrayerRequestAcceptance(models.Model):
    """Model for tracking which users have accepted a prayer request."""

    prayer_request = models.ForeignKey(
        PrayerRequest,
        on_delete=models.CASCADE,
        related_name='acceptances',
        help_text='The prayer request that was accepted'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='accepted_prayer_requests',
        help_text='User who accepted the prayer request'
    )
    counts_for_milestones = models.BooleanField(
        default=True,
        help_text='Whether this acceptance should count toward milestone tracking'
    )
    accepted_at = models.DateTimeField(
        auto_now_add=True,
        help_text='When the user accepted the prayer request'
    )

    class Meta:
        ordering = ['-accepted_at']
        unique_together = ('prayer_request', 'user')
        verbose_name = 'Prayer Request Acceptance'
        verbose_name_plural = 'Prayer Request Acceptances'
        indexes = [
            models.Index(fields=['user', '-accepted_at']),
            models.Index(fields=['prayer_request', '-accepted_at']),
        ]

    def __str__(self):
        return f'{self.user.email} accepted "{self.prayer_request.title}"'


class PrayerRequestPrayerLog(models.Model):
    """Model for tracking when users pray for a prayer request."""

    prayer_request = models.ForeignKey(
        PrayerRequest,
        on_delete=models.CASCADE,
        related_name='prayer_logs',
        help_text='The prayer request that was prayed for'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='prayer_logs',
        help_text='User who prayed'
    )
    prayed_on_date = models.DateField(
        help_text='The date the user prayed for this request',
        db_index=True
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='When this log entry was created'
    )

    class Meta:
        ordering = ['-prayed_on_date', '-created_at']
        unique_together = ('prayer_request', 'user', 'prayed_on_date')
        verbose_name = 'Prayer Request Prayer Log'
        verbose_name_plural = 'Prayer Request Prayer Logs'
        indexes = [
            models.Index(fields=['user', '-prayed_on_date']),
            models.Index(fields=['prayer_request', 'prayed_on_date']),
        ]

    def __str__(self):
        return f'{self.user.email} prayed for "{self.prayer_request.title}" on {self.prayed_on_date}'


class FeastPrayer(models.Model):
    """Prayer templates associated with feast designations.

    Stores prayer templates with {feast_name} placeholder that gets
    substituted at runtime with the actual feast name.
    """

    designation = models.CharField(
        max_length=256,
        choices=[
            ('Sundays, Dominical Feast Days', 'Sundays, Dominical Feast Days'),
            ('St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets',
             'St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets'),
            ('Patriarchs, Vartapets', 'Patriarchs, Vartapets'),
            ('Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord',
             'Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord'),
            ('Martyrs', 'Martyrs'),
        ],
        unique=True,
        db_index=True,
        help_text='Feast designation this prayer is for'
    )
    title = models.CharField(
        max_length=200,
        help_text='Prayer title template (can include {feast_name} placeholder)'
    )
    text = models.TextField(
        help_text='Prayer text template with {feast_name} placeholder'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Translations for bilingual support
    i18n = TranslationField(fields=('title', 'text'))

    class Meta:
        ordering = ['designation']
        verbose_name = 'Feast Prayer'
        verbose_name_plural = 'Feast Prayers'

    def __str__(self):
        return f"Prayer for {self.designation}"

    def render_for_feast(self, feast, lang='en'):
        """Render the prayer with feast name substituted.

        Args:
            feast: Feast instance
            lang: Language code ('en' or 'hy')

        Returns:
            dict with 'title' and 'text' keys containing rendered prayer
        """
        from django.utils.translation import activate

        # Activate the requested language
        activate(lang)

        # Get translated feast name
        feast_name = getattr(feast, 'name_i18n', feast.name)

        # Get translated prayer fields
        title_template = getattr(self, 'title_i18n', self.title)
        text_template = getattr(self, 'text_i18n', self.text)

        # Substitute {feast_name} placeholder
        rendered_title = title_template.replace('{feast_name}', feast_name) if title_template else ''
        rendered_text = text_template.replace('{feast_name}', feast_name) if text_template else ''

        return {
            'title': rendered_title,
            'text': rendered_text
        }
