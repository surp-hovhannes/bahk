"""Explicit moderation assignments; no implicit staff or superuser access."""

from django.conf import settings
from django.db import models


class Responsibility(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    general = models.BooleanField(default=False)
    crisis = models.BooleanField(default=False)

    def __str__(self):
        return str(self.user)


class Review(models.Model):
    kind = models.CharField(max_length=20)
    object_id = models.PositiveBigIntegerField()
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    outcome = models.CharField(max_length=30)
    note = models.TextField(blank=True)
    expected_outcome = models.CharField(max_length=80, blank=True)
    regression_reference = models.CharField(max_length=200, blank=True)
    signal_version = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["kind", "object_id"], name="moderation__kind_c8a34e_idx")]
