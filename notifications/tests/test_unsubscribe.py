from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.signing import TimestampSigner
import base64
import hmac
import hashlib

from hub.models import User, Profile, Church
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

    def test_unsubscribe_with_missing_token(self):
        """Test unsubscribing with a missing token."""
        response = self.client.get(reverse('notifications:unsubscribe'))
        
        # Check redirect to error page
        self.assertRedirects(response, reverse('notifications:unsubscribe_error'))
        
        # Check that preferences were not updated
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
        response = self.client.get(reverse('notifications:unsubscribe_success'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'notifications/unsubscribe_success.html')

    def test_unsubscribe_error_page(self):
        """Test the unsubscribe error page."""
        response = self.client.get(reverse('notifications:unsubscribe_error'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'notifications/unsubscribe_error.html')

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