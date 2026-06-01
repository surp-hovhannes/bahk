from django.apps import AppConfig


class IconsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'icons'

    def ready(self):
        import icons.signals  # noqa: F401
