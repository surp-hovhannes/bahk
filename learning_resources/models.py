from django.db import models
from django.utils.text import slugify
from PIL import Image
from io import BytesIO
from django.core.files import File
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
import os

class Video(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
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

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Articles'
