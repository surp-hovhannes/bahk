from django.db import models

# Create your models here.

class DeviceToken(models.Model):
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.token

    class Meta:
        ordering = ['-created_at']
