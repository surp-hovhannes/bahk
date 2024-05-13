"""Models for bahk hub."""
from django.contrib.auth.models import User
from django.db import models


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
    more_info_url = models.URLField(max_length=200, blank=True)
    
    def __str__(self):
        return f"{{self.name}} of the {{self.church.name}}"
