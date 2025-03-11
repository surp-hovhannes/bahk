"""Models for bahk hub."""
import logging
from django.utils import timezone
from model_utils import FieldTracker

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import constraints
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill, ResizeToFit
from imagekit.processors import Transpose

from hub.constants import CATENA_ABBREV_FOR_BOOK, CATENA_HOME_PAGE_URL, DAYS_TO_CACHE_THUMBNAIL
import bahk.settings as settings
from learning_resources.models import Video


class Church(models.Model):
    """Model for a church."""
    name = models.CharField(max_length=128, unique=True)

    @classmethod
    def get_default_pk(cls):
        church, _ = cls.objects.get_or_create(
            name=settings.DEFAULT_CHURCH_NAME
        )
        return church.pk

    def __str__(self):
        return self.name


class Fast(models.Model):
    """Model for a fast."""
    name = models.CharField(max_length=128)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="fasts")
    description = models.TextField(null=True, blank=True)
    culmination_feast = models.CharField(max_length=128, null=True, blank=True)
    culmination_feast_date = models.DateField(null=True, blank=True,
                                              help_text="You can enter in day/month/year format, e.g., 8/15/24")
    # auto-saved to be the year of the first day of the fast
    year = models.IntegerField(
        validators=[
            MinValueValidator(2024), 
            MaxValueValidator(3000)
        ],
        null=True,
        blank=True,
    )
    image = models.ImageField(upload_to='fast_images/', null=True, blank=True)
    image_thumbnail = ImageSpecField(source='image',
                                     processors=[Transpose(), ResizeToFit(800, None)],
                                     format='JPEG',
                                     options={'quality': 60})
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    
    # 2048 chars is the maximum URL length on Google Chrome
    url = models.URLField(verbose_name="Link to learn more", null=True, blank=True, max_length=2048,
                          help_text="URL to a link to learn more--must include protocol (e.g. https://)")

    # Track changes to the image field
    tracker = FieldTracker(fields=['image'])

    def save(self, **kwargs):
        # First check if this is a new instance or if the image field has changed
        is_new_image = (
            self._state.adding or
            'image' in kwargs.get('update_fields', []) or
            (not self._state.adding and self.tracker.has_changed('image'))
        )
        super().save(**kwargs)
        
        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.image:
            # Update cache if:
            # 1. No cached URL exists
            # 2. Image was changed/uploaded
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
                    thumbnail = self.image_thumbnail.generate()
                    
                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.image_thumbnail.url
                    self.cached_thumbnail_updated = timezone.now()
                    
                    # Save again to update the cache fields only
                    super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
                except Exception as e:
                    logging.error(f"Error caching S3 thumbnail URL for Fast {self.id}: {e}")
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
        
        # Update year if days exist
        if self.days.exists():
            self.year = self.days.first().date.year
            super().save(update_fields=["year"])

        # Invalidate the cache for this church's fast list
        from hub.views.fast import FastListView
        FastListView().invalidate_cache(self.church_id)

    def delete(self, *args, **kwargs):
        church_id = self.church_id
        super().delete(*args, **kwargs)
        # Invalidate the cache after deletion
        from hub.views.fast import FastListView
        FastListView().invalidate_cache(church_id)

    class Meta:
        constraints = [
            constraints.UniqueConstraint(fields=["name", "church", "year"], name="unique_name_church_year"),
            constraints.UniqueConstraint(fields=["culmination_feast_date", "church"], name="unique_feast_date_church"),
        ]

    @property
    def modal_id(self):
        return f"fastModal_{self.id}"

    def __str__(self):
        s = self.name
        if self.year:
            s += f" ({self.year})"
        return s


class Profile(models.Model):
    """Model for a user profile."""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=64, null=True, blank=True,
                            help_text="Name (first, last, whatever you want to be known as)")
    church = models.ForeignKey(Church, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    fasts = models.ManyToManyField(Fast, related_name="profiles")
    location = models.CharField(max_length=100, blank=True, null=True) 
    profile_image = models.ImageField(upload_to='profile_images/originals/', null=True, blank=True)
    profile_image_thumbnail = ImageSpecField(source='profile_image',
                                             processors=[Transpose(), ResizeToFill(100, 100)],
                                             format='JPEG',
                                             options={'quality': 60})
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    receive_upcoming_fast_reminders = models.BooleanField(default=False)
    receive_upcoming_fast_push_notifications = models.BooleanField(default=True)
    receive_ongoing_fast_push_notifications = models.BooleanField(default=True)
    receive_daily_fast_push_notifications = models.BooleanField(default=False)
    include_weekly_fasts_in_notifications = models.BooleanField(default=False)

    # Track changes to the profile_image field
    tracker = FieldTracker(fields=['profile_image'])

    def save(self, **kwargs):
        # First check if this is a new instance or if the profile image field has changed
        is_new_image = (
            self._state.adding or
            'profile_image' in kwargs.get('update_fields', []) or
            (not self._state.adding and self.tracker.has_changed('profile_image'))
        )
        super().save(**kwargs)
        
        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.profile_image:
            # Update cache if:
            # 1. No cached URL exists
            # 2. Image was changed/uploaded
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
                    thumbnail = self.profile_image_thumbnail.generate()
                    
                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.profile_image_thumbnail.url
                    self.cached_thumbnail_updated = timezone.now()
                    
                    # Save again to update the cache fields only
                    super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
                except Exception as e:
                    logging.error(f"Error caching S3 thumbnail URL for Profile {self.id}: {e}")
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])

    def __str__(self):
        return self.user.email


class Day(models.Model):
    """Model for a day in time."""
    date = models.DateField()
    fast = models.ForeignKey(Fast, on_delete=models.CASCADE, null=True, related_name="days")
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="days", default=Church.get_default_pk)

    def __str__(self):
        return f'{self.date.strftime("%Y-%m-%d")} ({f"{self.fast.name}, " if self.fast else ""}{self.church.name})'
    

class DevotionalSet(models.Model):
    """Model for an ordered collection of devotionals."""
    title = models.CharField(max_length=128)

    @property
    def number_of_days(self):
        return self.devotionals.count()

    def __str__(self):
        return f"{self.title} ({self.number_of_days} days)"


class Devotional(models.Model):
    """Stores content for a daily devotional."""
    day = models.ForeignKey(
        Day, 
        help_text="Day for devotional (ensure that it belongs to proper church calendar)",
        on_delete=models.CASCADE, 
        related_name="devotionals"
    )
    description = models.TextField(null=True, blank=True)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="devotionals")
    devotional_set = models.ForeignKey(
        DevotionalSet,
        help_text="Set that this devotional belongs to. If none, it is a standalone devotional.",
        on_delete=models.CASCADE, 
        related_name="devotionals",
        null=True, 
        blank=True
    )
    order = models.PositiveIntegerField(
        help_text="If part of a set, the order of the devotional in the set", 
        null=True, 
        blank=True
    )

    def save(self, *args, **kwargs):
        # Set video category to 'devotional' before saving
        if self.video and self.video.category != 'devotional':
            self.video.category = 'devotional'
            self.video.save()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['order']
        unique_together = [['devotional_set', 'order']]


class Reading(models.Model):
    """Stores details for a Bible reading."""
    day = models.ForeignKey(Day, on_delete=models.CASCADE, related_name="readings")
    book = models.CharField(max_length=64)
    start_chapter = models.IntegerField(verbose_name="Start Chapter")
    start_verse = models.IntegerField(verbose_name="Start Verse")
    end_chapter = models.IntegerField(verbose_name="End Chapter", help_text="May be same as start chapter")
    end_verse = models.IntegerField(verbose_name="End Verse", help_text="May be same as end verse")

    class Meta:
        constraints = [
            constraints.UniqueConstraint(
                fields=["day", "book", "start_chapter", "start_verse", "end_chapter", "end_verse"], 
                name="unique_reading_per_day"
            ),
        ]


    def create_url(self):
        """Creates URL to read the reading."""
        book_abbrev = CATENA_ABBREV_FOR_BOOK.get(self.book)
        if book_abbrev is None:
            logging.error("Missing Catena URL abbreviation for %s. Returning home page", self.book)
            return CATENA_HOME_PAGE_URL
        verse_ref = "" if self.start_verse <= 2 else f"#{book_abbrev}{self.start_chapter:03d}{self.start_verse - 2:03d}"
        return f"{CATENA_HOME_PAGE_URL}{book_abbrev}/{self.start_chapter:d}/{verse_ref}"


    def __str__(self):
        s = f"{self.book}: Chapter {self.start_chapter}, "
        if self.start_chapter == self.end_chapter and self.start_verse != self.end_verse:
            s += f"Verses {self.start_verse}-{self.end_verse}"
        else:
            s += f"Verse {self.start_verse}"
            if self.start_chapter != self.end_chapter:
                s += f" - Chapter {self.end_chapter}, Verse {self.end_verse}"
        return s