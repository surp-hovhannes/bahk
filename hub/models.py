"""Models for bahk hub."""

import logging

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import constraints
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill, ResizeToFit, Transpose
from model_utils.tracker import FieldTracker
from modeltrans.fields import TranslationField
from taggit.managers import TaggableManager

import bahk.settings as settings
from hub.constants import (
    AZARIAH_CATENA_BOOK,
    AZARIAH_CATENA_CHAPTER,
    AZARIAH_TO_DANIEL_VERSE_OFFSET,
    CATENA_ABBREV_FOR_BOOK_NORMALIZED,
    CATENA_HOME_PAGE_URL,
    DAYS_TO_CACHE_THUMBNAIL,
)
from learning_resources.models import Video
from learning_resources.utils import devotional_set_image_upload_path


class Church(models.Model):
    """Model for a church."""

    name = models.CharField(max_length=128, unique=True)

    @classmethod
    def get_default_pk(cls):
        church, _ = cls.objects.get_or_create(name=settings.DEFAULT_CHURCH_NAME)
        return church.pk

    def __str__(self):
        return self.name


class Fast(models.Model):
    """Model for a fast."""

    name = models.CharField(max_length=128)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="fasts")
    description = models.TextField(null=True, blank=True)
    culmination_feast = models.CharField(max_length=128, null=True, blank=True)
    culmination_feast_date = models.DateField(
        null=True,
        blank=True,
        help_text="You can enter in day/month/year format, e.g., 8/15/24",
    )
    culmination_feast_salutation = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        help_text="Greeting or salutation for the culmination feast"
    )
    culmination_feast_message = models.TextField(
        null=True,
        blank=True,
        help_text="Message for the culmination feast"
    )
    culmination_feast_message_attribution = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        help_text="Attribution or author of the culmination feast message"
    )
    has_day_zero = models.BooleanField(
        default=False,
        help_text="If True, the fast starts counting from Day 0 instead of Day 1",
    )

    # auto-saved to be the year of the first day of the fast
    year = models.IntegerField(
        validators=[MinValueValidator(2024), MaxValueValidator(3000)],
        null=True,
        blank=True,
    )
    image = models.ImageField(upload_to="fast_images/", null=True, blank=True)
    image_thumbnail = ImageSpecField(
        source="image",
        processors=[Transpose(), ResizeToFit(800, None)],
        format="JPEG",
        options={"quality": 60},
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)

    # Translations for user-facing fields
    i18n = TranslationField(fields=(
        'name',
        'description',
        'culmination_feast',
        'culmination_feast_salutation',
        'culmination_feast_message',
        'culmination_feast_message_attribution',
    ))

    # 2048 chars is the maximum URL length on Google Chrome
    url = models.URLField(
        verbose_name="Link to learn more",
        null=True,
        blank=True,
        max_length=2048,
        help_text="URL to a link to learn more--must include protocol (e.g. https://)",
    )

    # Track changes to the image field
    tracker = FieldTracker(fields=["image"])

    def save(self, **kwargs):
        # First check if this is a new instance or if the image field has changed
        is_new_image = (
            self._state.adding
            or "image" in kwargs.get("update_fields", [])
            or (not self._state.adding and self.tracker.has_changed("image"))
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
                    thumbnail = self.image_thumbnail.generate()

                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.image_thumbnail.url
                    self.cached_thumbnail_updated = timezone.now()

                    # Save again to update the cache fields only
                    super().save(
                        update_fields=[
                            "cached_thumbnail_url",
                            "cached_thumbnail_updated",
                        ]
                    )
                except Exception as e:
                    logging.error(
                        f"Error caching S3 thumbnail URL for Fast {self.id}: {e}"
                    )
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(
                    update_fields=["cached_thumbnail_url", "cached_thumbnail_updated"]
                )

        # Update year if days exist
        if self.days.exists():
            first_day = self.days.order_by("date").first()
            if first_day and self.year != first_day.date.year:
                self.year = first_day.date.year
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
            constraints.UniqueConstraint(
                fields=["name", "church", "year"], name="unique_name_church_year"
            ),
            constraints.UniqueConstraint(
                fields=["culmination_feast_date", "church"],
                name="unique_feast_date_church",
            ),
        ]
        indexes = [
            models.Index(fields=["church"]),
            models.Index(fields=["culmination_feast_date"]),
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
    name = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Name (first, last, whatever you want to be known as)",
    )
    church = models.ForeignKey(
        Church,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profiles",
    )
    fasts = models.ManyToManyField(Fast, related_name="profiles")
    location = models.CharField(max_length=100, blank=True, null=True)
    # Store geocoded coordinates for performance
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    # User's timezone in IANA format (e.g., 'America/New_York')
    timezone = models.CharField(
        max_length=100,
        default='UTC',
        help_text="User's timezone in IANA format (e.g., 'America/New_York')"
    )
    profile_image = models.ImageField(
        upload_to="profile_images/originals/", null=True, blank=True
    )
    profile_image_thumbnail = ImageSpecField(
        source="profile_image",
        processors=[Transpose(), ResizeToFill(100, 100)],
        format="JPEG",
        options={"quality": 60},
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    receive_upcoming_fast_reminders = models.BooleanField(default=False)
    receive_upcoming_fast_push_notifications = models.BooleanField(default=True)
    receive_ongoing_fast_push_notifications = models.BooleanField(default=True)
    receive_daily_fast_push_notifications = models.BooleanField(default=False)
    receive_weekly_prayer_request_push_notifications = models.BooleanField(default=False)
    include_weekly_fasts_in_notifications = models.BooleanField(default=False)
    # Email preferences
    receive_promotional_emails = models.BooleanField(
        default=True, help_text="Receive promotional emails"
    )
    # Attribution fields for campaign/source tracking
    utm_source = models.CharField(
        max_length=255, null=True, blank=True, help_text="Last seen UTM source"
    )
    utm_campaign = models.CharField(
        max_length=255, null=True, blank=True, help_text="Last seen UTM campaign"
    )
    join_source = models.CharField(
        max_length=255, null=True, blank=True, help_text="Join source such as push,email,social"
    )

    # Track changes to the profile_image field
    tracker = FieldTracker(fields=["profile_image", "location", "timezone"])

    def geocode_location(self):
        """
        Geocode the location and store coordinates.

        Called when location is updated to maintain latitude/longitude.
        Uses AWS Location Service with fallback to cached locations.

        Note: This is now a simple wrapper that schedules an asynchronous task
        to avoid blocking the user interface during geocoding.
        """
        from django.conf import settings
        from hub.tasks import geocode_profile_location

        if not getattr(settings, "GEOCODING_ENABLED", True):
            return

        if not self.location:
            self.latitude = None
            self.longitude = None
            return

        # Schedule the geocoding task asynchronously
        # Only do this if the profile has been saved (has an ID)
        if self.id is not None:
            geocode_profile_location.delay(self.id, self.location)

    def save(self, **kwargs):
        # First check if this is a new instance or if the profile image field has changed
        is_new_image = (
            self._state.adding
            or "profile_image" in kwargs.get("update_fields", [])
            or (not self._state.adding and self.tracker.has_changed("profile_image"))
        )

        # Check if location has changed
        location_changed = (
            self._state.adding
            or "location" in kwargs.get("update_fields", [])
            or self.tracker.has_changed("location")
        )

        # Call the parent save method
        super().save(**kwargs)

        # Handle thumbnail URL caching after the instance and image are fully saved to S3
        if self.profile_image:
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
                    thumbnail = self.profile_image_thumbnail.generate()

                    # Get the S3 URL after the file has been uploaded
                    self.cached_thumbnail_url = self.profile_image_thumbnail.url
                    self.cached_thumbnail_updated = timezone.now()

                    # Save again to update the cache fields only
                    super().save(
                        update_fields=[
                            "cached_thumbnail_url",
                            "cached_thumbnail_updated",
                        ]
                    )
                except Exception as e:
                    logging.error(
                        f"Error caching S3 thumbnail URL for Profile {self.id}: {e}"
                    )
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(
                    update_fields=["cached_thumbnail_url", "cached_thumbnail_updated"]
                )

        # If location changed, trigger async geocoding
        if location_changed and self.location:
            self.geocode_location()

    def __str__(self):
        return self.user.email


class Day(models.Model):
    """Model for a day in time."""

    date = models.DateField()
    fast = models.ForeignKey(
        Fast, on_delete=models.CASCADE, null=True, related_name="days"
    )
    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name="days",
        default=Church.get_default_pk,
    )

    def __str__(self):
        return f'{self.date.strftime("%Y-%m-%d")} ({f"{self.fast.name}, " if self.fast else ""}{self.church.name})'

    class Meta:
        indexes = [
            models.Index(fields=["fast", "date"]),
            models.Index(fields=["date"]),
            models.Index(fields=["church", "date"]),
        ]


class DevotionalSet(models.Model):
    """Model for an ordered collection of devotionals based on a fast."""

    title = models.CharField(max_length=128)
    description = models.TextField(
        null=True,
        blank=True,
        help_text="Description of the devotional set"
    )
    fast = models.ForeignKey(
        Fast,
        on_delete=models.CASCADE,
        related_name="devotional_sets",
        help_text="The fast this devotional set is associated with"
    )
    image = models.ImageField(
        upload_to=devotional_set_image_upload_path,
        null=True,
        blank=True,
        help_text="Image for the devotional set. Recommended size: 1600x1200 pixels (4:3)"
    )
    thumbnail = ImageSpecField(
        source="image",
        processors=[ResizeToFill(400, 300)],  # 4:3 aspect ratio
        format="JPEG",
        options={"quality": 85}
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    # Translations for user-facing fields
    i18n = TranslationField(fields=(
        'title',
        'description',
    ))

    # Track changes to the image field
    tracker = FieldTracker(fields=["image"])

    def save(self, **kwargs):
        # First check if this is a new instance or if the image field has changed
        is_new_image = (
            self._state.adding
            or "image" in kwargs.get("update_fields", [])
            or (not self._state.adding and self.tracker.has_changed("image"))
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
                            "cached_thumbnail_url",
                            "cached_thumbnail_updated",
                        ]
                    )
                except Exception as e:
                    logging.error(
                        f"Error caching S3 thumbnail URL for DevotionalSet {self.id}: {e}"
                    )
        else:
            # Clear cached URL if image is removed
            if self.cached_thumbnail_url or self.cached_thumbnail_updated:
                self.cached_thumbnail_url = None
                self.cached_thumbnail_updated = None
                super().save(
                    update_fields=["cached_thumbnail_url", "cached_thumbnail_updated"]
                )

    # Class-level cache invalidation counter
    _cache_version = 0

    @classmethod
    def invalidate_all_number_of_days_cache(cls):
        """Invalidate the cached number of days count for all instances."""
        cls._cache_version += 1

    def invalidate_number_of_days_cache(self):
        """Invalidate the cached number of days count for this instance."""
        if hasattr(self, '_number_of_days_cache'):
            delattr(self, '_number_of_days_cache')

    @property
    def number_of_days(self):
        """Get number of devotionals associated with this set's fast."""
        # Check if we need to invalidate cache based on class version
        if not hasattr(self, '_cache_version') or self._cache_version != DevotionalSet._cache_version:
            if hasattr(self, '_number_of_days_cache'):
                delattr(self, '_number_of_days_cache')
            self._cache_version = DevotionalSet._cache_version

        if not hasattr(self, '_number_of_days_cache'):
            if self.fast:
                self._number_of_days_cache = Devotional.objects.filter(day__fast=self.fast).count()
            else:
                self._number_of_days_cache = 0
        return self._number_of_days_cache

    def __str__(self):
        return f"{self.title} ({self.number_of_days} days)"

    class Meta:
        ordering = ['-created_at']


class Devotional(models.Model):
    """Stores content for a daily devotional."""

    day = models.ForeignKey(
        Day,
        help_text="Day for devotional (ensure that it belongs to proper church calendar)",
        on_delete=models.CASCADE,
        related_name="devotionals",
    )
    description = models.TextField(null=True, blank=True)
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="devotionals"
    )
    order = models.PositiveIntegerField(
        help_text="Only matters when multiple devotionals exist on the same day. "
        "Controls display order and must be unique per day and language.",
        null=True,
        blank=True,
    )
    # Language of this devotional content
    language_code = models.CharField(max_length=5, default='en')

    # Translations for user-facing fields
    i18n = TranslationField(fields=(
        'description',
    ))

    def save(self, *args, **kwargs):
        # Set video category to 'devotional' before saving
        if self.video and self.video.category != "devotional":
            self.video.category = "devotional"
            self.video.save()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["day__date", "order"]
        unique_together = (("day", "order", "language_code"),)


# Signal handlers to invalidate DevotionalSet cache when devotionals change
@receiver([post_save, post_delete], sender=Devotional)
def invalidate_devotional_set_cache(sender, instance, **kwargs):
    """
    Invalidate the number_of_days cache for all DevotionalSets associated with
    the fast when a devotional is created, updated, or deleted.
    """
    if instance.day and instance.day.fast:
        # Invalidate cache for all DevotionalSet instances (both in memory and future fetches)
        DevotionalSet.invalidate_all_number_of_days_cache()

class Reading(models.Model):
    """Stores details for a Bible reading."""

    day = models.ForeignKey(Day, on_delete=models.CASCADE, related_name="readings")
    sequence = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Position of this reading within its day's list, as returned by the "
        "armenian_lectionary engine. Determines display order."
    )
    book = models.CharField(max_length=64)
    start_chapter = models.IntegerField(verbose_name="Start Chapter")
    start_verse = models.IntegerField(verbose_name="Start Verse")
    end_chapter = models.IntegerField(
        verbose_name="End Chapter", help_text="May be same as start chapter"
    )
    end_verse = models.IntegerField(
        verbose_name="End Verse", help_text="May be same as end verse"
    )

    # LEGACY -- superseded by PassageText, which stores text per (passage, language).
    # Nothing reads these; they are retained for one release so a revert is a one-line
    # change in get_reading_text_fields, and are dropped in a follow-up migration.
    # Do not add code that depends on them.
    _LEGACY_TEXT_HELP = "LEGACY, unused -- text now lives in PassageText."

    text = models.TextField(blank=True, default="", help_text=_LEGACY_TEXT_HELP)
    text_copyright = models.TextField(blank=True, default="", help_text=_LEGACY_TEXT_HELP)
    text_version = models.CharField(
        max_length=16, blank=True, default="", help_text=_LEGACY_TEXT_HELP,
    )
    text_fetched_at = models.DateTimeField(
        null=True, blank=True, help_text=_LEGACY_TEXT_HELP,
    )
    fums_token = models.TextField(blank=True, default="", help_text=_LEGACY_TEXT_HELP)

    text_hy_version = models.CharField(
        max_length=64, blank=True, default="", help_text=_LEGACY_TEXT_HELP,
    )
    text_hy_copyright = models.TextField(
        blank=True, default="", help_text=_LEGACY_TEXT_HELP,
    )
    text_hy_fetched_at = models.DateTimeField(
        null=True, blank=True, help_text=_LEGACY_TEXT_HELP,
    )
    text_hy_fums_token = models.TextField(
        blank=True, default="", help_text=_LEGACY_TEXT_HELP,
    )

    # Which passage this reading cites, independent of language or edition.  Rows sharing
    # a key share one retrieval per language (see hub.constants.passage_key and PassageText).
    # Derived from book/chapter/verse; recomputed on every save().
    passage_key = models.CharField(
        max_length=64, blank=True, default="", editable=False, db_index=True,
        help_text=(
            "Passage identity, e.g. 'GEN.1.1-1.5'. Readings sharing this key share one "
            "fetched text per language. Empty when the book name has no USFM mapping, "
            "i.e. when no retrieval is possible."
        ),
    )

    # Translations for user-facing fields.  `text` is deliberately absent: translated text
    # is keyed by passage in PassageText, not stashed per row in this JSON column.
    i18n = TranslationField(fields=('book',))

    class Meta:
        ordering = ["day__date", "sequence", "id"]
        constraints = [
            constraints.UniqueConstraint(
                fields=[
                    "day",
                    "book",
                    "start_chapter",
                    "start_verse",
                    "end_chapter",
                    "end_verse",
                ],
                name="unique_reading_per_day",
            ),
        ]

    def save(self, *args, **kwargs):
        """Keep ``passage_key`` in step with the citation fields.

        Done here rather than at each ``get_or_create`` call site so the admin (which
        exposes ``book_en`` for editing and saves via ``ModelAdmin.save_model``) and any
        future creation path stay correct.  A stale key would silently split one dedup
        group in two and double retrieval spend, so it must not depend on callers
        remembering.

        ``bulk_create`` and ``QuerySet.update()`` bypass this, as they bypass every
        ``save()``; neither is used to write Reading's citation fields today.  If that
        changes, compute the key there too — or run ``backfill_reading_passage_keys``.
        """
        from hub.constants import passage_key as compute_passage_key

        key = compute_passage_key(
            self.book, self.start_chapter, self.start_verse,
            self.end_chapter, self.end_verse,
        )
        if key != self.passage_key:
            self.passage_key = key
            # Widen a narrow update_fields so a key repaired on an otherwise-unrelated
            # save (e.g. the book_hy writes that pass update_fields=['i18n']) persists.
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = list(update_fields) + ["passage_key"]
        super().save(*args, **kwargs)

    def create_url(self):
        """Creates URL to read the reading.

        Uses normalized book name lookup to handle curly/smart quote
        variations returned by scrapers (e.g. ' vs U+2019).
        """
        from hub.constants import normalize_book_name

        book, start_chapter, start_verse = self.book, self.start_chapter, self.start_verse
        if normalize_book_name(book) == "Azariah":
            # See hub.constants.AZARIAH_TO_DANIEL_VERSE_OFFSET: Catena has no standalone
            # "Azariah" section, so remap onto its Daniel 3 home before the normal lookup.
            book = AZARIAH_CATENA_BOOK
            start_chapter = AZARIAH_CATENA_CHAPTER
            start_verse = start_verse + AZARIAH_TO_DANIEL_VERSE_OFFSET

        normalized_book = normalize_book_name(book)
        book_abbrev = CATENA_ABBREV_FOR_BOOK_NORMALIZED.get(normalized_book)
        if book_abbrev is None:
            logging.error(
                "Missing Catena URL abbreviation for %r (normalized: %r). Returning home page",
                self.book,
                normalized_book,
            )
            return CATENA_HOME_PAGE_URL
        verse_ref = (
            ""
            if start_verse <= 2
            else f"#{book_abbrev}{start_chapter:03d}{start_verse - 2:03d}"
        )
        return f"{CATENA_HOME_PAGE_URL}{book_abbrev}/{start_chapter:d}/{verse_ref}"

    # Add a helper property to easily reference the passage in a standard format
    @property
    def passage_reference(self) -> str:
        """Return a string reference like 'John 3:16-18'."""
        if self.start_chapter == self.end_chapter:
            if self.start_verse == self.end_verse:
                return f"{self.book} {self.start_chapter}:{self.start_verse}"
            else:
                return f"{self.book} {self.start_chapter}:{self.start_verse}-{self.end_verse}"
        else:
            return f"{self.book} {self.start_chapter}:{self.start_verse}-{self.end_chapter}:{self.end_verse}"

    @property
    def active_context(self):
        """Return the active context for the reading."""
        return self.contexts.filter(active=True).first()

    def __str__(self):
        s = f"{self.book}: Chapter {self.start_chapter}, "
        if (
            self.start_chapter == self.end_chapter
            and self.start_verse != self.end_verse
        ):
            s += f"Verses {self.start_verse}-{self.end_verse}"
        else:
            s += f"Verse {self.start_verse}"
            if self.start_chapter != self.end_chapter:
                s += f" - Chapter {self.end_chapter}, Verse {self.end_verse}"
        return s


class Feast(models.Model):
    """A commemoration, and the enrichment the app keeps for it.

    Keyed by ``(church, name)``, not by date.  The name of the day comes from the
    ``armenian_lectionary`` engine and is recomputed per request, so nothing here needs to be
    pre-populated for a date to resolve; what this row exists to hold is the part the engine has
    no notion of -- the AI ``designation``, the matched ``icon``, and the generated ``contexts``.

    Those are properties of the commemoration, not of the day it lands on.  This model used to
    hang off ``Day``, which meant the same feast earned a new row, a new LLM context and a new
    icon match every year it recurred; the engine emits a few hundred distinct names across its
    whole supported range, so the table was almost entirely duplicates.  Same reasoning as
    ``PassageText``: key by the thing, never by the date.
    """

    class Designation(models.TextChoices):
        SUNDAYS_DOMINICAL = (
            'Sundays, Dominical Feast Days',
            'Sundays, Dominical Feast Days'
        )
        ST_GREGORY_APOSTLES = (
            'St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets',
            'St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets'
        )
        PATRIARCHS_VARTAPETS = (
            'Patriarchs, Vartapets',
            'Patriarchs, Vartapets'
        )
        NATIVITY_MOTHER_OF_GOD = (
            'Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord',
            'Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord'
        )
        MARTYRS = (
            'Martyrs',
            'Martyrs'
        )
        FAST = (
            'Fast',
            'Fast'
        )

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="feasts")
    # 512 instead of 256 because two feast names in the Armenian lectionary exceed 256 characters:
    # the Twelve Holy Doctors (289) and the Holy Fathers of Egypt (257)
    name = models.CharField(max_length=512)
    designation = models.CharField(
        max_length=256,
        choices=Designation.choices,
        null=True,
        blank=True,
        help_text="Classification of the feast, automatically determined by AI"
    )
    icon = models.ForeignKey(
        'icons.Icon',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='feasts',
        help_text="Matched icon for this feast"
    )

    # Translations for user-facing fields
    i18n = TranslationField(fields=('name',))

    class Meta:
        constraints = [
            # One row per commemoration per church: the invariant the whole re-key exists to
            # establish, enforced in the database so a race between two requests for the same
            # date cannot recreate the per-occurrence duplication.
            models.UniqueConstraint(
                fields=["church", "name"], name="unique_feast_per_church"
            ),
        ]

    @property
    def active_context(self):
        """Return the active context for the feast."""
        return self.contexts.filter(active=True).first()

    def __str__(self):
        return f"{self.name} ({self.church.name})"


class FastParticipantMap(models.Model):
    """Stores metadata about the generated participant maps."""

    fast = models.ForeignKey(
        Fast, on_delete=models.CASCADE, related_name="participant_maps"
    )
    map_file = models.FileField(upload_to="fast_maps/", null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    participant_count = models.IntegerField(default=0)
    format = models.CharField(max_length=10, default="svg")  # 'png' or 'svg'

    @property
    def map_url(self):
        """Return the URL to the map file."""
        if self.map_file:
            return self.map_file.url
        return None

    def __str__(self):
        return f"Map for {self.fast} ({self.last_updated.strftime('%Y-%m-%d %H:%M')})"

    class Meta:
        indexes = [
            models.Index(fields=["fast"]),
            models.Index(fields=["last_updated"]),
        ]


class GeocodingCache(models.Model):
    """
    Cache for geocoded locations to avoid repeated API calls.

    This stores the mapping between location text and coordinates,
    significantly reducing the need for external geocoding API calls.
    """

    location_text = models.CharField(max_length=255, unique=True, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    last_updated = models.DateTimeField(auto_now=True)
    error_count = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Geocoding Cache"
        verbose_name_plural = "Geocoding Cache"

    def __str__(self):
        return f"{self.location_text} ({self.latitude}, {self.longitude})"


class LLMPrompt(models.Model):
    """Model for storing LLM prompts used to generate content."""

    MODEL_CHOICES = [
        ("o4-mini", "o4 Mini"),
        ("gpt-4o-mini", "GPT 4o Mini"),
        ("gpt-5-mini", "GPT 5 Mini"),
        ("gpt-5-nano", "GPT 5 Nano"),
        ("gpt-5-mini-2025-08-07", "GPT 5 Mini (2025-08-07)"),
        ("gpt-5", "GPT 5"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
        ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet")
    ]

    APPLIES_TO_CHOICES = [
        ("readings", "Readings"),
        ("feasts", "Feasts"),
        ("prayer_requests", "Prayer Requests"),
        ("intentions", "Intentions"),
    ]

    model = models.CharField(
        max_length=32,
        choices=MODEL_CHOICES,
        help_text="The LLM model used for generation",
    )
    role = models.TextField(help_text="The role/persona for the model")
    prompt = models.TextField(help_text="The actual prompt text")
    applies_to = models.CharField(
        max_length=32,
        choices=APPLIES_TO_CHOICES,
        default="readings",
        help_text="The type of content this prompt is used for",
    )
    active = models.BooleanField(
        default=False,
        help_text="If True, this prompt is the one currently used for generation",
    )

    def save(self, *args, **kwargs):
        """Override save to ensure only one prompt can be active per applies_to type."""
        if self.active:
            # Check if there are any other active prompts for the same applies_to type
            other_active_prompts = LLMPrompt.objects.filter(
                active=True, applies_to=self.applies_to
            ).exclude(pk=self.pk)
            if other_active_prompts.exists():
                raise ValidationError(
                    f"Another LLM prompt for {self.applies_to} is already marked as active. "
                    "Please deactivate it first."
                )
        super().save(*args, **kwargs)

    def get_llm_service(self):
        """
        Get the appropriate LLM service based on the model type.
        Returns:
            LLMService: An instance of the appropriate service class.
        Raises:
            ValueError: If the model type is not supported.
        """
        from hub.services.llm_service import OpenAIService, AnthropicService

        if self.model.startswith(("gpt", "o1", "o3", "o4")):
            return OpenAIService()
        elif "claude" in self.model:
            return AnthropicService()
        else:
            raise ValueError(f"Unsupported model: {self.model}")

    def __str__(self):
        status = " (Active)" if self.active else ""
        return f"{self.model} prompt for {self.applies_to}: {self.prompt[:20]}{status}"


class ReadingContext(models.Model):
    """Model for storing context for Bible readings, typically generated by an LLM."""

    reading = models.ForeignKey(
        Reading,
        on_delete=models.CASCADE,
        related_name="contexts",
        help_text="The reading this context is for",
    )
    text = models.TextField(help_text="The generated context text")
    prompt = models.ForeignKey(
        LLMPrompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The prompt used to generate this context",
    )
    thumbs_up = models.PositiveIntegerField(
        default=0, help_text="Number of thumbs-up votes for the context"
    )
    thumbs_down = models.PositiveIntegerField(
        default=0, help_text="Number of thumbs-down votes for the context"
    )
    time_of_generation = models.DateTimeField(
        auto_now_add=True,
        null=True,
        blank=True,
        help_text="Timestamp when the context was generated",
    )
    active = models.BooleanField(
        default=True, help_text="Whether the context is currently active"
    )

    # Translations for user-facing fields
    i18n = TranslationField(fields=('text',))

    def save(self, *args, **kwargs):
        if self.active:
            # Deactivate any other active context for this reading
            ReadingContext.objects.filter(reading=self.reading, active=True).exclude(
                pk=self.pk
            ).update(active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Context for {self.reading}: {self.text[:100]}"


class FeastContext(models.Model):
    """Model for storing context for feast days, typically generated by an LLM."""

    feast = models.ForeignKey(
        Feast,
        on_delete=models.CASCADE,
        related_name="contexts",
        help_text="The feast this context is for",
    )
    text = models.TextField(help_text="The generated context text (longer format)")
    short_text = models.TextField(
        help_text="The generated short context text (2 sentence summary)"
    )
    prompt = models.ForeignKey(
        LLMPrompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The prompt used to generate this context",
    )
    thumbs_up = models.PositiveIntegerField(
        default=0, help_text="Number of thumbs-up votes for the context"
    )
    thumbs_down = models.PositiveIntegerField(
        default=0, help_text="Number of thumbs-down votes for the context"
    )
    time_of_generation = models.DateTimeField(
        auto_now_add=True,
        null=True,
        blank=True,
        help_text="Timestamp when the context was generated",
    )
    active = models.BooleanField(
        default=True, help_text="Whether the context is currently active"
    )

    # Translations for user-facing fields
    i18n = TranslationField(fields=('text', 'short_text'))

    def save(self, *args, **kwargs):
        if self.active:
            # Deactivate any other active context for this feast
            FeastContext.objects.filter(feast=self.feast, active=True).exclude(
                pk=self.pk
            ).update(active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Context for {self.feast}: {self.text[:100]}"


class PatristicQuote(models.Model):
    """Model for storing patristic quotes from Church Fathers and Saints."""

    text = models.TextField(
        help_text="Quote text in Markdown format"
    )
    attribution = models.CharField(
        max_length=256,
        help_text="Source or author of the quote (e.g., 'St. John Chrysostom', 'The Desert Fathers')"
    )
    churches = models.ManyToManyField(
        Church,
        related_name='patristic_quotes',
        help_text='Churches this quote belongs to'
    )
    fasts = models.ManyToManyField(
        Fast,
        blank=True,
        related_name='patristic_quotes',
        help_text='Optional fasts this quote is associated with'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Tags using django-taggit
    tags = TaggableManager(
        blank=True,
        help_text='Tags for categorizing quotes (e.g., prayer, fasting, humility)'
    )

    # Translations for user-facing fields
    i18n = TranslationField(fields=(
        'text',
        'attribution',
    ))

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Patristic Quote'
        verbose_name_plural = 'Patristic Quotes'
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['attribution']),
        ]

    def __str__(self):
        # Return first 50 characters of the quote text
        from django.utils.text import Truncator
        return f"{Truncator(self.text).chars(50)} - {self.attribution}"



class FastIntention(models.Model):
    """Stores a user's spiritual intention for a fast.

    Intentions can be private (only the user sees them) or public
    (visible to other participants on the participant sheet).
    Soft-delete via is_active=False on leave so rejoining restores.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='fast_intentions',
    )
    fast = models.ForeignKey(
        Fast,
        on_delete=models.CASCADE,
        related_name='intentions',
    )
    text = models.CharField(
        max_length=280,
        blank=True,
        default='',
        help_text='The intention text (max 280 characters)',
    )
    is_public = models.BooleanField(
        default=False,
        help_text='Whether this intention is visible to other fast participants',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Soft-delete flag; set to False when user leaves the fast',
    )
    matched_tags = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text='LLM-derived prayer tags for this intention; null until computed',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'fast'],
                condition=models.Q(is_active=True),
                name='one_active_intention_per_user_fast',
            ),
        ]
        indexes = [
            models.Index(fields=['fast', 'is_active', 'is_public']),
            models.Index(fields=['user', 'fast']),
        ]
        ordering = ['-created_at']
        verbose_name = 'Fast Intention'
        verbose_name_plural = 'Fast Intentions'

    def __str__(self):
        visibility = 'public' if self.is_public else 'private'
        status = 'active' if self.is_active else 'inactive'
        return f"Intention for {self.fast.name} by {self.user.username} ({visibility}, {status})"


class BibleVerse(models.Model):
    """A single Bible verse, addressable by ``(version, book, chapter, verse)``.

    This is the offline corpus that replaces per-request scraping of
    sacredtradition.am for Armenian reading text.  The corpus is derived from
    verse-keyed, per-book JSON (git-tracked source of truth) and loaded here via
    the ``load_bible_hy`` management command.

    Verses are keyed by their *actual* number rather than by position, so
    translations with non-contiguous versification (e.g. Tobit 1, which skips
    verses 11/14/16/17) are represented faithfully.  Chapter superscriptions
    (Psalm titles and the like) are stored as ``verse=0``.
    """

    #: Eastern Armenian translation served by sacredtradition.am.
    NOR_EJMIATSIN = "Նոր Էջմիածին"

    version = models.CharField(
        max_length=32,
        default=NOR_EJMIATSIN,
        help_text="Translation identifier, e.g. 'Նոր Էջմիածին' (Eastern Armenian).",
    )
    book = models.CharField(
        max_length=3,
        help_text=(
            "USFM book id, e.g. 'GEN', '1CO' (see hub.constants.BOOK_NAME_TO_USFM). "
            "The corpus is a superset of that map: a few unread deuterocanonical "
            "books (e.g. '1ES', '3MA') have no BOOK_NAME_TO_USFM entry."
        ),
    )
    chapter = models.PositiveSmallIntegerField()
    verse = models.PositiveSmallIntegerField(
        help_text="Verse number; 0 denotes a chapter superscription/title.",
    )
    text = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "book", "chapter", "verse"],
                name="unique_bible_verse",
            ),
        ]
        # The unique constraint's composite btree index also backs the
        # (version, book, chapter, verse) range scans used to compose a reading,
        # so no additional index is required.
        ordering = ["version", "book", "chapter", "verse"]
        verbose_name = "Bible Verse"
        verbose_name_plural = "Bible Verses"

    def __str__(self):
        return f"{self.book} {self.chapter}:{self.verse} ({self.version})"

    @classmethod
    def passage_queryset(
        cls, version, book, start_chapter, start_verse, end_chapter, end_verse,
    ):
        """Return the verses of a reading range as an ordered queryset.

        Expressed as a single indexed range scan.  The same-chapter case (the
        vast majority of readings) is a plain bounded filter; multi-chapter
        ranges take the start-chapter tail, any whole middle chapters, and the
        end-chapter head.

        Chapter superscriptions (``verse=0``) are excluded — readings never
        carry them.  They remain in the corpus for a future effort that wants
        them.
        """
        from django.db.models import Q

        qs = cls.objects.filter(version=version, book=book)
        if start_chapter == end_chapter:
            rng = Q(chapter=start_chapter, verse__gte=start_verse, verse__lte=end_verse)
        else:
            rng = (
                Q(chapter=start_chapter, verse__gte=start_verse)
                | Q(chapter__gt=start_chapter, chapter__lt=end_chapter)
                | Q(chapter=end_chapter, verse__lte=end_verse)
            )
        return qs.filter(rng).exclude(verse=0).order_by("chapter", "verse")

    @classmethod
    def compose_passage(
        cls, version, book, start_chapter, start_verse, end_chapter, end_verse,
    ):
        """Return the reading text with inline ``[verse]`` markers, or '' if absent.

        The output mirrors the historical sacredtradition.am format
        (``[1] … [2] …``) so downstream rendering is unchanged.
        """
        rows = cls.passage_queryset(
            version, book, start_chapter, start_verse, end_chapter, end_verse,
        )
        parts = [f"[{r.verse}] {r.text}" for r in rows]
        return " ".join(parts).strip()


class PassageText(models.Model):
    """Retrieved text for one passage in one language.

    Text is stored per *passage*, not per ``Reading`` row.  The Armenian lectionary
    assigns ~1,100 distinct passages across all years, but emits ~1,500 readings a year
    forever, so a row-keyed cache makes retrieval cost grow without bound while a
    passage-keyed one is a constant.  That is the difference between exhausting
    API.Bible's monthly quota and using a quarter of it.

    One table serves every language.  Adding a language means inserting rows and
    registering a fetcher — not adding columns to ``Reading`` — which is what keeps a
    future source with its own retrieval limits from needing a schema change.
    ``fums_token`` stays empty for languages composed from a local corpus.
    """

    passage_key = models.CharField(
        max_length=64, db_index=True,
        help_text="Passage identity from hub.constants.passage_key, e.g. 'GEN.1.1-1.5'.",
    )
    language = models.CharField(
        max_length=8,
        help_text="ISO 639-1 code, e.g. 'en', 'hy'.",
    )
    text = models.TextField(blank=True, default="")
    version = models.CharField(
        max_length=64, blank=True, default="",
        help_text=(
            "Edition the text came from, e.g. 'NKJV', 'KJVAIC', 'Նոր Էջմիածին'. A column "
            "rather than part of the key because it varies within a language: English "
            "uses NKJV for canonical books and KJVAIC for the Apocrypha."
        ),
    )
    copyright = models.TextField(
        blank=True, default="",
        help_text="Copyright notice, stored exactly as the source returned it.",
    )
    fums_token = models.TextField(
        blank=True, default="",
        help_text="API.Bible FUMS v3 token, where the source issues one.",
    )
    fetched_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            "When the text was retrieved. NULL means the passage is known but has never "
            "been fetched — how the warm-up enumerates work for the refresh task."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["passage_key", "language"],
                name="unique_passage_text_per_language",
            ),
        ]
        indexes = [
            # Backs the refresh task's "what is stale in this language" scan.
            models.Index(fields=["language", "fetched_at"], name="passage_text_lang_fresh_idx"),
        ]
        verbose_name = "Passage Text"
        verbose_name_plural = "Passage Texts"

    def __str__(self):
        return f"{self.passage_key} ({self.language})"

    def is_expired(self, *, now=None) -> bool:
        """True when this text is past its source's freshness cap.

        A missing ``fetched_at`` counts as expired: we cannot show the text is fresh
        enough to serve, so it is treated the same as stale.  Languages absent from
        ``LANGUAGE_TEXT_MAX_AGE_DAYS`` never expire — a locally composed corpus has no
        licence clock.
        """
        # django.conf.settings, not the module-level `bahk.settings` bound at the top of
        # this file: only the former honours override_settings in tests.
        from datetime import timedelta

        from django.conf import settings as django_settings

        max_age = getattr(django_settings, "LANGUAGE_TEXT_MAX_AGE_DAYS", {}).get(self.language)
        if max_age is None:
            return False
        if self.fetched_at is None:
            return True
        return self.fetched_at < (now or timezone.now()) - timedelta(days=max_age)
