from django.apps import AppConfig


class LearningResourcesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "learning_resources"
    
    def ready(self):
        """Import signals when the app is ready."""
        try:
            from . import signals  # Import signals module to register signal handlers
        except ImportError:
            pass