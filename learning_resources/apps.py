from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class LearningResourcesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "learning_resources"

    def ready(self):
        """Import signals and install test-only offline video storage."""
        if getattr(settings, "OFFLINE_VIDEO_STORAGE", False) and settings.IS_PRODUCTION:
            raise ImproperlyConfigured(
                "OFFLINE_VIDEO_STORAGE must not be enabled in production."
            )

        if getattr(settings, "OFFLINE_VIDEO_STORAGE", False):
            from storages.backends.s3 import S3Storage

            class OfflineS3Storage(S3Storage):
                def _save(self, name, content):
                    return name

                def exists(self, name):
                    return False

                def url(self, name, parameters=None, expire=None, http_method=None):
                    return f"{settings.MEDIA_URL}{name}"

            from .models import Video

            Video._meta.get_field("video").storage = OfflineS3Storage()

        try:
            from . import signals  # Import signals module to register signal handlers
        except ImportError:
            pass