from django.db import models
from markdownx.models import MarkdownxField
from markdownx.utils import markdownify

# Create your models here.
class Changelog(models.Model):
    """Model for tracking changes or updates in the app."""
    title = models.CharField(max_length=255)
    description = MarkdownxField()
    version = models.CharField(max_length=50)
    date = models.DateField(auto_now_add=True)

    @property
    def formatted_description(self):
        return markdownify(self.description)

    def __str__(self):
        return f"{self.title} - {self.version}"