from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from hub.models import Day
from notifications.models import PostFastEmailDelivery, PostFastEncouragementEmail
from notifications.tasks import send_post_fast_encouragement_task
from tests.fixtures.test_data import TestDataFactory


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_RATE_LIMIT=100,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class PostFastEncouragementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.church = TestDataFactory.create_church()
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.day = Day.objects.create(
            church=self.church, fast=self.fast,
            date=timezone.localdate() - timedelta(days=1),
        )
        self.profile = TestDataFactory.create_profile(church=self.church)
        self.profile.fasts.add(self.fast)
        self.email_copy = PostFastEncouragementEmail.objects.create(
            fast=self.fast, subject='A custom blessing', message='May Christ grant you peace.',
        )

    def test_post_fast_email_fires_for_qualifying_users(self):
        self.assertEqual(send_post_fast_encouragement_task(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.profile.user.email])
        self.assertEqual(mail.outbox[0].subject, 'A custom blessing')
        self.assertIn('May Christ grant you peace.', mail.outbox[0].body)
        self.assertIn('Unsubscribe', mail.outbox[0].body)
        self.assertIsNotNone(PostFastEmailDelivery.objects.get().sent_at)
        self.assertEqual(send_post_fast_encouragement_task(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_skips_opt_outs(self):
        self.profile.receive_promotional_emails = False
        self.profile.save()
        self.assertEqual(send_post_fast_encouragement_task(), 0)

    def test_custom_fast_copy_overrides_default(self):
        self.email_copy.delete()
        PostFastEncouragementEmail.objects.create(subject='Default', message='Default message')
        PostFastEncouragementEmail.objects.create(
            fast=self.fast, subject='A special blessing', message='Custom encouragement <script>bad</script>',
        )
        self.assertEqual(send_post_fast_encouragement_task(), 1)
        self.assertEqual(mail.outbox[0].subject, 'A special blessing')
        self.assertIn('Custom encouragement', mail.outbox[0].body)
        self.assertNotIn('<script>', mail.outbox[0].alternatives[0].content)
        self.assertIn('Unsubscribe', mail.outbox[0].body)

    def test_legacy_default_is_not_sent(self):
        self.email_copy.delete()
        PostFastEncouragementEmail.objects.create(subject='Default blessing', message='Keep praying.')
        self.assertEqual(send_post_fast_encouragement_task(), 0)
        self.assertFalse(PostFastEmailDelivery.objects.exists())

    def test_no_custom_message_sends_no_email(self):
        self.email_copy.delete()
        self.assertEqual(send_post_fast_encouragement_task(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(PostFastEmailDelivery.objects.exists())

    def test_blank_custom_message_sends_no_email(self):
        self.email_copy.message = '  \n '
        self.email_copy.save()
        self.assertEqual(send_post_fast_encouragement_task(), 0)
        self.assertFalse(PostFastEmailDelivery.objects.exists())

    def test_admin_requires_fast(self):
        from notifications.admin import PostFastEncouragementEmailForm

        form = PostFastEncouragementEmailForm(data={'subject': 'Hello', 'message': 'Peace'})
        self.assertFalse(form.is_valid())
        self.assertIn('fast', form.errors)

    def test_skips_users_who_left(self):
        self.profile.fasts.remove(self.fast)
        self.assertEqual(send_post_fast_encouragement_task(), 0)

    def test_only_sends_after_final_day(self):
        Day.objects.create(church=self.church, fast=self.fast, date=timezone.localdate())
        self.assertEqual(send_post_fast_encouragement_task(), 0)

    def test_skips_older_completions(self):
        self.day.date -= timedelta(days=1)
        self.day.save()
        self.assertEqual(send_post_fast_encouragement_task(), 0)

    def test_failed_send_can_be_retried(self):
        with patch('notifications.tasks.EmailMultiAlternatives.send', side_effect=RuntimeError('offline')):
            self.assertEqual(send_post_fast_encouragement_task(), 0)
        self.assertFalse(PostFastEmailDelivery.objects.filter(sent_at__isnull=False).exists())
        self.assertEqual(send_post_fast_encouragement_task(), 1)

    def test_zero_send_does_not_mark_delivered(self):
        with patch('notifications.tasks.EmailMultiAlternatives.send', return_value=0):
            self.assertEqual(send_post_fast_encouragement_task(), 0)
        self.assertIsNone(PostFastEmailDelivery.objects.get().sent_at)

    def test_rate_limit_defers_delivery(self):
        cache.set('email_count', 100)
        with patch.object(send_post_fast_encouragement_task, 'apply_async') as schedule:
            self.assertEqual(send_post_fast_encouragement_task(), 0)
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.kwargs['kwargs'], {'completed_on': self.day.date.isoformat()})
        self.assertFalse(PostFastEmailDelivery.objects.filter(sent_at__isnull=False).exists())

    def test_delayed_batch_preserves_completion_date(self):
        self.day.date -= timedelta(days=1)
        self.day.save()
        self.assertEqual(send_post_fast_encouragement_task(completed_on=self.day.date.isoformat()), 1)

    def test_default_and_fast_copy_are_unique(self):
        self.email_copy.delete()
        for fast in [None, self.fast]:
            with self.subTest(fast=fast):
                PostFastEncouragementEmail.objects.create(fast=fast, subject='First', message='Hello')
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PostFastEncouragementEmail.objects.create(fast=fast, subject='Second', message='Hello')

    def test_skips_inactive_users_and_empty_email(self):
        user = self.profile.user
        user.is_active = False
        user.save()
        self.assertEqual(send_post_fast_encouragement_task(), 0)
        user.is_active = True
        user.email = ''
        user.save()
        self.assertEqual(send_post_fast_encouragement_task(), 0)
