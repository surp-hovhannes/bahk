from django.db import models
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
from django.utils import timezone
import logging
from s3_file_field import S3FileField

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
        help_text='Recommended size: 720x1280 pixels (portrait)',
        null=True,
        blank=True
    )
    video = S3FileField(
        upload_to='videos/',
        help_text='Supported formats: MP4, WebM. Portrait orientation (9:16). Files up to 20MB.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Generate a smaller version of the thumbnail for previews
    thumbnail_small = ImageSpecField(
        source='thumbnail',
        processors=[ResizeToFill(169, 300)],  # 9:16 aspect ratio (portrait)
        format='JPEG',
        options={'quality': 85}
    )
    # Cache the thumbnail URL to avoid S3 calls
    cached_thumbnail_url = models.URLField(max_length=2048, null=True, blank=True)
    cached_thumbnail_updated = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Track if the thumbnail has changed
        if not self._state.adding and self.pk:
            try:
                old_instance = Video.objects.get(pk=self.pk)
                thumbnail_changed = old_instance.thumbnail != self.thumbnail
            except Video.DoesNotExist:
                thumbnail_changed = True
        else:
            # New instance
            thumbnail_changed = self.thumbnail is not None
        
        # Check if update_fields is specified and contains 'thumbnail'
        update_fields = kwargs.get('update_fields', None)
        if update_fields is not None:
            thumbnail_explicitly_updated = 'thumbnail' in update_fields
        else:
            thumbnail_explicitly_updated = False
        
        # Thumbnail is new/changed if it's a new instance, explicitly updated, or detected as changed
        is_new_image = self._state.adding or thumbnail_explicitly_updated or thumbnail_changed
        
        # Call the parent save method
        super().save(*args, **kwargs)
        
        if self.thumbnail:
            should_update_cache = (
                not self.cached_thumbnail_url or
                is_new_image or
                (self.cached_thumbnail_updated and 
                 timezone.now() - self.cached_thumbnail_updated > timezone.timedelta(days=DAYS_TO_CACHE_THUMBNAIL))
            )
            
            if should_update_cache:
                try:
                    thumbnail = self.thumbnail_small.generate()
                    self.cached_thumbnail_url = self.thumbnail_small.url
                    self.cached_thumbnail_updated = timezone.now()
                    super().save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
                except Exception as e:
                    logging.error(f"Error caching S3 thumbnail URL for Video {self.id}: {e}")
        else:
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

    def save(self, *args, **kwargs):
        # Track if the image has changed
        if not self._state.adding and self.pk:
            try:
                old_instance = Article.objects.get(pk=self.pk)
                image_changed = old_instance.image != self.image
            except Article.DoesNotExist:
                image_changed = True
        else:
            # New instance
            image_changed = self.image is not None
        
        # Check if update_fields is specified and contains 'image'
        update_fields = kwargs.get('update_fields', None)
        if update_fields is not None:
            image_explicitly_updated = 'image' in update_fields
        else:
            image_explicitly_updated = False
        
        # Image is new/changed if it's a new instance, explicitly updated, or detected as changed
        is_new_image = self._state.adding or image_explicitly_updated or image_changed
        
        # Call the parent save method
        super().save(*args, **kwargs)
        
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
                 timezone.now() - self.cached_thumbnail_updated > timezone.timedelta(days=DAYS_TO_CACHE_THUMBNAIL))
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


class Recipe(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    image = models.ImageField(
        upload_to='recipes/images/',
        help_text='Main recipe image. Recommended size: 1600x1200 pixels (4:3)'
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
    # recipe-specific fields
    time_required = models.CharField("Time required to make recipe", max_length=64)
    serves = models.CharField("Number of servings", max_length=32)
    ingredients = models.TextField(
        help_text='Recipe ingredients in Markdown format',
        verbose_name='Ingredients'  # This overrides the label
    )
    directions = models.TextField(
        help_text='Recipe directions in Markdown format',
        verbose_name='Directions'  # This overrides the label
    )

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
                    logging.error(f"Error caching S3 thumbnail URL for Recipe {self.id}: {e}")
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
        verbose_name_plural = 'Recipes'


# Translation models for internationalization
class VideoTranslation(models.Model):
    video = models.ForeignKey(Video, related_name='translations', on_delete=models.CASCADE)
    language_code = models.CharField(max_length=10, help_text='Language code (e.g., en, am)')
    title = models.CharField(max_length=200)
    description = models.TextField()
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('video', 'language_code')
        ordering = ['-created_at']
        verbose_name_plural = 'Video Translations'
    
    def __str__(self):
        return f"{self.video.title} ({self.language_code})"


class ArticleTranslation(models.Model):
    article = models.ForeignKey(Article, related_name='translations', on_delete=models.CASCADE)
    language_code = models.CharField(max_length=10, help_text='Language code (e.g., en, am)')
    title = models.CharField(max_length=200)
    body = models.TextField(help_text='Content in Markdown format')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('article', 'language_code')
        ordering = ['-created_at']
        verbose_name_plural = 'Article Translations'
    
    def __str__(self):
        return f"{self.article.title} ({self.language_code})"


class RecipeTranslation(models.Model):
    recipe = models.ForeignKey(Recipe, related_name='translations', on_delete=models.CASCADE)
    language_code = models.CharField(max_length=10, help_text='Language code (e.g., en, am)')
    title = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    time_required = models.CharField("Time required to make recipe", max_length=64)
    serves = models.CharField("Number of servings", max_length=32)
    ingredients = models.TextField(
        help_text='Recipe ingredients in Markdown format',
        verbose_name='Ingredients'
    )
    directions = models.TextField(
        help_text='Recipe directions in Markdown format',
        verbose_name='Directions'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('recipe', 'language_code')
        ordering = ['-created_at']
        verbose_name_plural = 'Recipe Translations'
    
    def __str__(self):
        return f"{self.recipe.title} ({self.language_code})"
    