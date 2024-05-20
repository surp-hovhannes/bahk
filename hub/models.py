"""Models for bahk hub."""
from django.contrib.auth.models import User
from django.db import models
from PIL import Image
from PIL import ImageOps
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
import mimetypes


class Church(models.Model):
    """Model for a church."""
    name = models.CharField(max_length=128)

    def __str__(self):
        return self.name


class Fast(models.Model):
    """Model for a fast."""
    name = models.CharField(max_length=128)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="fasts")
    description = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to='fast_images/', null=True, blank=True) 

    def __str__(self):
        return f"{self.name} of the {self.church.name}"


class Profile(models.Model):
    """Model for a user profile.
    
    Based on https://simpleisbetterthancomplex.com/tutorial/2016/07/22/how-to-extend-django-user-model.html#onetoone
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    church = models.ForeignKey(Church, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    fasts = models.ManyToManyField(Fast, related_name="profiles")
    profile_image = models.ImageField(upload_to='profile_images/', null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.profile_image:
            self.profile_image = self.resize_image(self.profile_image, size=(500, 500))
        super().save(*args, **kwargs)

    def resize_image(self, image, size=(300, 300)):
        img = Image.open(image)
        img = ImageOps.exif_transpose(img)
        img.thumbnail(size, Image.LANCZOS)
        buffer = BytesIO()
        img_format = img.format if img.format else 'JPEG'
        img.save(buffer, format=img_format)
        content_type, _ = mimetypes.guess_type(image.name)
        return InMemoryUploadedFile(buffer, 'ImageField', image.name, content_type, buffer.tell(), None)
    
    def get_image_url(self, size):
        return f"/profile_image/{self.pk}/{size[0]}x{size[1]}/"

    def __str__(self):
        return self.user.username


class Day(models.Model):
    """Model for a day in time."""
    date = models.DateField()
    fasts = models.ManyToManyField(Fast, related_name="days")

    def __str__(self):
        return self.date.strftime("%B-%d-%Y")
