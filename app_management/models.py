from django.db import models
from django.utils import timezone
from markdownx.models import MarkdownxField
from markdownx.utils import markdownify

# Create your models here.
class Changelog(models.Model):
    """Deprecated in-app changelog; GitHub releases are the canonical release record."""
    title = models.CharField(max_length=255)
    description = MarkdownxField()
    version = models.CharField(max_length=50)
    date = models.DateField(default=timezone.localdate)

    @property
    def formatted_description(self):
        return markdownify(self.description)

    def __str__(self):
        return f"{self.title} - {self.version}"
