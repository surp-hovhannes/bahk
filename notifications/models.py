from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class DeviceToken(models.Model):
    IOS = 'ios'
    ANDROID = 'android'
    WEB = 'web'
    DEVICE_TYPES = [
        (IOS, 'iOS'),
        (ANDROID, 'Android'),
        (WEB, 'Web'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='device_tokens', null=True)
    token = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=10, choices=DEVICE_TYPES, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.token

    class Meta:
        ordering = ['-created_at']
