from django.apps import AppConfig


class ModerationConfig(AppConfig):
    name = "moderation"

    def ready(self):
        from django.db.models.signals import post_save
        from icons.models import IconFeedback
        from moderation.signals import icon_submitted

        post_save.connect(icon_submitted, sender=IconFeedback, dispatch_uid="moderation.icon_submitted")
