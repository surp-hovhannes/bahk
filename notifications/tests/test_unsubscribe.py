import time
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.signing import TimestampSigner

from tests.fixtures.test_data import TestDataFactory

User = get_user_model()

@override_settings(
    SITE_URL='http://testserver',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com'
)
class UnsubscribeTests(TestCase):
    """Tests for the unsubscribe functionality."""

    def setUp(self):
        """Set up test data."""
        # Create church using TestDataFactory
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create user using TestDataFactory
        self.user = TestDataFactory.create_user(
            username="testuser@example.com",
            email="testuser@example.com",
            password="testpass123"
        )
        
        # Create profile with promotional emails enabled using TestDataFactory
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            receive_promotional_emails=True
        )
        
        # Generate valid token
        signer = TimestampSigner()
        self.valid_token = signer.sign(self.user.id)
        
        # Create client
        self.client = Client()

    def test_unsubscribe_with_valid_token(self):
        """Test unsubscribing with a valid token."""
        response = self.client.get(reverse('notifications:unsubscribe'), {'token': self.valid_token})
        
        # Check redirect to success page
        self.assertRedirects(response, reverse('notifications:unsubscribe_success'))
        
        # Check that preferences were updated
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.receive_promotional_emails)

    def test_unsubscribe_with_invalid_token(self):
        """Test unsubscribing with an invalid token."""
        invalid_token = 'invalid-token'
        response = self.client.get(reverse('notifications:unsubscribe'), {'token': invalid_token})
        
        # Check redirect to error page
        self.assertRedirects(response, reverse('notifications:unsubscribe_error'))
        
        # Check that preferences were not updated
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.receive_promotional_emails)

    def test_unsubscribe_with_tampered_token(self):
        """Test unsubscribing with a token whose signed value was changed."""
        tampered_token = self.valid_token.replace(str(self.user.id), str(self.user.id + 1), 1)
        response = self.client.get(reverse('notifications:unsubscribe'), {'token': tampered_token})

        self.assertRedirects(response, reverse('notifications:unsubscribe_error'))

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.receive_promotional_emails)

    def test_unsubscribe_with_missing_token(self):
        """Test unsubscribing with a missing token."""
        response = self.client.get(reverse('notifications:unsubscribe'))
        
        # Check redirect to error page
        self.assertRedirects(response, reverse('notifications:unsubscribe_error'))
        
        # Check that preferences were not updated
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.receive_promotional_emails)

    def test_unsubscribe_with_expired_token(self):
        """Test unsubscribing with an expired token."""
        eight_days_later = time.time() + (60 * 60 * 24 * 8)

        with patch('django.core.signing.time.time', return_value=eight_days_later):
            response = self.client.get(
                reverse('notifications:unsubscribe'),
                {'token': self.valid_token}
            )

        self.assertRedirects(response, reverse('notifications:unsubscribe_error'))

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.receive_promotional_emails)

    def test_unsubscribe_with_nonexistent_user(self):
        """Test unsubscribing with a token for a nonexistent user."""
        # Create token for nonexistent user ID
        signer = TimestampSigner()
        token = signer.sign(99999)
        
        response = self.client.get(reverse('notifications:unsubscribe'), {'token': token})
        
        # Check redirect to error page
        self.assertRedirects(response, reverse('notifications:unsubscribe_error'))
        
        # Check that preferences were not updated
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.receive_promotional_emails)

    def test_unsubscribe_success_page(self):
        """Test the unsubscribe success page."""
        url = reverse('notifications:unsubscribe_success')
        self.assertEqual(url, '/hub/notifications/unsubscribe/success/')

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'notifications/unsubscribe_success.html')
        self.assertContains(response, "Successfully Unsubscribed")

    def test_unsubscribe_error_page(self):
        """Test the unsubscribe error page."""
        url = reverse('notifications:unsubscribe_error')
        self.assertEqual(url, '/hub/notifications/unsubscribe/error/')

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'notifications/unsubscribe_error.html')
        self.assertContains(response, "Unsubscribe Error")

    def test_resubscribe_functionality(self):
        """Test that a user can resubscribe after unsubscribing."""
        # First unsubscribe
        self.client.get(reverse('notifications:unsubscribe'), {'token': self.valid_token})
        
        # Check that user is unsubscribed
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.receive_promotional_emails)
        
        # Update profile to resubscribe
        self.profile.receive_promotional_emails = True
        self.profile.save()
        
        # Check that user is resubscribed
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.receive_promotional_emails) 

    def test_unsubscribe_with_valid_token_is_idempotent(self):
        """Test repeated unsubscribe requests leave the user unsubscribed."""
        url = reverse('notifications:unsubscribe')

        first_response = self.client.get(url, {'token': self.valid_token})
        second_response = self.client.get(url, {'token': self.valid_token})

        self.assertRedirects(first_response, reverse('notifications:unsubscribe_success'))
        self.assertRedirects(second_response, reverse('notifications:unsubscribe_success'))

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.receive_promotional_emails)
