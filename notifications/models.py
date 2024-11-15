from django.db import models
import re

# Create your models here.

class DeviceToken(models.Model):
    DEVICE_TYPES = (
        ('ios', 'iOS'),
        ('android', 'Android'),
    )
    
    token = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=10, choices=DEVICE_TYPES, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        pattern = r'^ExponentPushToken\[[a-zA-Z0-9_-]+\]$'
        if not bool(re.match(pattern, self.token)):
            raise ValidationError('Invalid Expo push token format')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.token

    class Meta:
        ordering = ['-created_at']
