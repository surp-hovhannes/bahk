"""The visual fixtures must be repeatable and must never dispatch work or use IO."""

from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.test import TestCase, override_settings

from events.models import Event
from hub.models import Fast, Profile


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class SeedVisualQATests(TestCase):
    def test_repeatable_and_offline(self):
        def snapshot():
            return {model._meta.label: list(model.objects.order_by("pk").values()) for model in apps.get_models()}

        with ExitStack() as stack:
            # Fail immediately, rather than letting a swallowed IO exception hide a call.
            guards = [
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
                for target in (
                    "socket.socket.connect",
                    "requests.sessions.Session.request",
                    "celery.app.task.Task.apply_async",
                    "django.core.mail.EmailMessage.send",
                    "storages.backends.s3.S3Storage._save",
                    "storages.backends.s3.S3Storage.url",
                )
            ]
            guards.extend(
                stack.enter_context(patch.object(signal, "send", side_effect=AssertionError("save signal")))
                for signal in (pre_save, post_save, m2m_changed)
            )
            call_command("seed_visual_qa", stdout=StringIO())
            first = snapshot()
            self.assertEqual(Event.objects.filter(title__startswith="Visual QA ").count(), 24)
            self.assertEqual(Profile.objects.filter(user__username__startswith="visual-qa-").count(), 3)
            self.assertEqual(Fast.objects.get(name="Visual QA Autumn Fast").days.count(), 7)
            self.assertFalse(
                Profile.objects.filter(user__username__startswith="visual-qa-", user__is_staff=True).exists()
            )
            call_command("seed_visual_qa", stdout=StringIO())
            self.assertEqual(snapshot(), first)
            for guard in guards:
                guard.assert_not_called()
