from datetime import datetime, timedelta
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from hub.models import Fast, Profile, Day, Church
from hub.utils import send_fast_reminders
from tests.fixtures.test_data import TestDataFactory

User = get_user_model()

@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
)
class TestSendFastReminders(TestCase):
    def setUp(self):
        # Create test user and profile using TestDataFactory
        self.user = TestDataFactory.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            receive_upcoming_fast_reminders=True
        )

        # Use the church from the profile created by TestDataFactory
        self.church = self.profile.church

        # Create a test fast using TestDataFactory
        self.fast = TestDataFactory.create_fast(
            name='Test Fast',
            church=self.church
        )
        
        # Create days for the fast (starting in 2 days)
        self.start_date = timezone.now().date() + timedelta(days=2)
        self.end_date = timezone.now().date() + timedelta(days=4)
        
        # Create days for the fast period using TestDataFactory
        current_date = self.start_date
        while current_date <= self.end_date:
            day = TestDataFactory.create_day(
                date=current_date,
                church=self.church
            )
            day.fast = self.fast
            day.save()
            current_date += timedelta(days=1)
        
        # Add profile to fast
        self.fast.profiles.add(self.profile)

    def test_send_fast_reminders(self):
        """Test that fast reminders are sent correctly."""
        # Execute the function
        send_fast_reminders()

        # Check that one email was sent
        self.assertEqual(len(mail.outbox), 1)
        
        # Verify email content
        email = mail.outbox[0]
        self.assertEqual(email.subject, f'Upcoming Fast: {self.fast.name}')
        self.assertEqual(email.to, [self.user.email])
        self.assertIn(self.fast.name, email.body)
        self.assertIn(self.fast.name, email.alternatives[0][0])  # HTML content

    def test_no_reminder_for_started_fast(self):
        """Test that reminders are not sent for fasts that have already started."""
        # Modify the fast days to have started yesterday
        Day.objects.filter(fast=self.fast).delete()
        
        start_date = timezone.now().date() - timedelta(days=1)
        end_date = timezone.now().date() + timedelta(days=1)
        
        current_date = start_date
        while current_date <= end_date:
            day = TestDataFactory.create_day(
                date=current_date,
                church=self.church
            )
            day.fast = self.fast
            day.save()
            current_date += timedelta(days=1)

        # Execute the function
        send_fast_reminders()

        # Check that no emails were sent
        self.assertEqual(len(mail.outbox), 0)

    def test_no_reminder_for_weekly_fast(self):
        """Test that reminders are not sent for weekly fasts."""
        # Modify the fast to be a weekly fast
        self.fast.name = 'Friday Fasts'
        self.fast.save()

        # Execute the function
        send_fast_reminders()

        # Check that no emails were sent
        self.assertEqual(len(mail.outbox), 0)

    def test_no_reminder_for_disabled_profile(self):
        """Test that reminders are not sent for profiles that have disabled reminders."""
        # Disable reminders for the profile
        self.profile.receive_upcoming_fast_reminders = False
        self.profile.save()

        # Execute the function
        send_fast_reminders()

        # Check that no emails were sent
        self.assertEqual(len(mail.outbox), 0)

    def test_reminder_for_multiple_fasts(self):
        """Test that reminders are sent for multiple upcoming fasts."""
        # Create another fast using TestDataFactory
        another_fast = TestDataFactory.create_fast(
            name='Another Test Fast',
            church=self.church
        )
        
        # Create days for the new fast (starting in 1 day)
        start_date = timezone.now().date() + timedelta(days=1)
        end_date = timezone.now().date() + timedelta(days=3)
        
        current_date = start_date
        while current_date <= end_date:
            day = TestDataFactory.create_day(
                date=current_date,
                church=self.church
            )
            day.fast = another_fast
            day.save()
            current_date += timedelta(days=1)
        
        # Add profile to the new fast
        another_fast.profiles.add(self.profile)

        # Execute the function
        send_fast_reminders()

        # Check that one email was sent (it should only send for the earliest fast)
        self.assertEqual(len(mail.outbox), 1)
        
        # Verify the email is for the earliest fast
        email = mail.outbox[0]
        self.assertEqual(email.subject, f'Upcoming Fast: {another_fast.name}') 

    def test_no_reminder_for_promo_email(self):
        """Test that reminders are not sent for fasts that have promotional emails assigned to them."""
        # Create a promotional email using TestDataFactory
        TestDataFactory.create_promo_email(fast=self.fast)
        
        # Execute the function
        send_fast_reminders()

        # Check that no emails were sent
        self.assertEqual(len(mail.outbox), 0)
