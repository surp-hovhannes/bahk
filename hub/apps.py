from django.apps import AppConfig


class HubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'hub'

    def ready(self):
        """
        Register signals when the app is ready.
        This ensures our signal handlers are connected.
        """
        # Configure global profanity filter overrides (used across multiple apps).
        from hub.profanity import configure_profanity_filter
        configure_profanity_filter()

        # Import signals to register them
        import hub.signals
