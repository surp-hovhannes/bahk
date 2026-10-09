from django.db import transaction
from moderation.notifications import notify


def icon_submitted(sender, instance, created, raw=False, **kwargs):
    if created and not raw and not instance.is_resolved:
        transaction.on_commit(lambda: notify("icon", instance.pk))
