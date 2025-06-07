from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse
from django.conf import settings
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.core.files.storage import FileSystemStorage
from tests.fixtures.test_data import TestDataFactory

class TestStaticFilesStorage(FileSystemStorage):
    """Mock storage that returns a dummy URL for static files."""
    def url(self, name):
        return f'http://testserver/static/{name}'

@override_settings(
    SITE_URL='http://testserver',
    FRONTEND_URL='http://testserver',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com',
    STATICFILES_STORAGE='hub.tests.test_email_templates.TestStaticFilesStorage',
    STATIC_URL='/static/'
)
class EmailTemplateTests(TestCase):
    def setUp(self):
        """Set up test data."""
        # Create test user using TestDataFactory
        self.user = TestDataFactory.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )
        
        # Base context for all email templates
        self.base_context = {
            'user': self.user,
            'site_url': settings.SITE_URL,
        }

    def test_password_reset_email_template_renders(self):
        """Test that the password reset email template renders correctly."""
        # Create reset URL
        reset_url = f"{settings.FRONTEND_URL}/reset-password/test-uid/test-token"
        
        # Add reset URL to context
        context = {
            **self.base_context,
            'reset_url': reset_url,
        }
        
        # Render template
        rendered = render_to_string('email/password_reset.html', context)
        
        # Check basic content
        self.assertIn('Password Reset', rendered)
        self.assertIn(self.user.username, rendered)
        self.assertIn(reset_url, rendered)
        self.assertIn('Reset Password', rendered)
        
        # Check branding elements
        self.assertIn('Fast & Pray', rendered)
        self.assertIn('fastandprayhelp@gmail.com', rendered)
        self.assertIn('fastandpray.app', rendered)
        
        # Check images
        self.assertIn('http://testserver/email_images/logo.png', rendered)
        self.assertIn('http://testserver/email_images/logoicon.png', rendered)
        
        # Check security message
        self.assertIn('This link will expire in 24 hours', rendered)
        self.assertIn('If you didn\'t request this password reset', rendered)

    def test_password_reset_email_template_with_missing_context(self):
        """Test that the password reset email template handles missing context variables."""
        # Render with minimal context
        context = {
            'site_url': settings.SITE_URL
        }
        
        rendered = render_to_string('email/password_reset.html', context)
        
        # Template should still render without errors
        self.assertIn('Password Reset', rendered)
        self.assertIn('Fast & Pray', rendered)
        self.assertNotIn('None', rendered)  # No undefined variables should be rendered 