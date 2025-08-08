from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'events'
    verbose_name = 'User Events'

    def ready(self):
        """
        Connect signals when the app is ready.
        This ensures our event tracking signals are properly registered.
        """
        import events.signals  # Import to register the signals
