from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

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
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'hub.tests.test_email_templates.TestStaticFilesStorage',
        },
    },
    STATIC_URL='/static/'
)
class EmailTemplateTests(TestCase):
    def setUp(self):
        """Set up test data."""
        # Create test user using TestDataFactory
        self.user = TestDataFactory.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123',
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

    def test_upcoming_fasts_reminder_email_template_renders_english_content(self):
        """Test the upcoming fast reminder template with English content."""
        fast = {
            'name': 'Lenten Fast',
            'start_date': '2026-02-16',
            'end_date': '2026-04-04',
            'image': 'https://example.com/lent.jpg',
            'participant_count': 3,
            'description': 'A season of prayer and fasting.',
        }

        rendered = render_to_string(
            'email/upcoming_fasts_reminder.html',
            {
                **self.base_context,
                'fast': fast,
            },
        )

        self.assertIn(f'Dear {self.user.username},', rendered)
        self.assertIn('There is a fast upcoming, which you have joined!', rendered)
        self.assertIn('Lenten Fast', rendered)
        self.assertIn('<strong>Dates:</strong> 2026-02-16 to 2026-04-04', rendered)
        self.assertIn('https://example.com/lent.jpg', rendered)
        self.assertIn('3 people are fasting with you!', rendered)
        self.assertIn('A season of prayer and fasting.', rendered)

        self.assertIn('http://testserver/email_images/logo.png', rendered)
        self.assertIn('http://testserver/email_images/logoicon.png', rendered)
        self.assertIn('fastandprayhelp@gmail.com', rendered)
        self.assertIn('fastandpray.app', rendered)
        self.assertIn('class="content"', rendered)
        self.assertIn('class="footer"', rendered)
        self.assertNotIn('Compiled with Bootstrap Email', rendered)
        self.assertNotIn('unsubscribe', rendered.lower())

    def test_upcoming_fasts_reminder_email_template_renders_armenian_content(self):
        """Test the upcoming fast reminder template with Armenian content."""
        fast = {
            'name': 'Մեծ Պահք',
            'start_date': '2026-02-16',
            'end_date': '2026-04-04',
            'image': '',
            'participant_count': 1,
            'description': 'Աղոթքի եւ պահքի շրջան։',
        }

        rendered = render_to_string(
            'email/upcoming_fasts_reminder.html',
            {
                'user': self.user,
                'fast': fast,
            },
        )

        self.assertIn('Մեծ Պահք', rendered)
        self.assertIn('Աղոթքի եւ պահքի շրջան։', rendered)
        self.assertIn('<strong>Dates:</strong> 2026-02-16 to 2026-04-04', rendered)
        self.assertNotIn('people are fasting with you!', rendered)
        self.assertNotIn('class="fast-image"', rendered)

        self.assertIn('https://fastandpray.app/email_images/logo.png', rendered)
        self.assertIn('https://fastandpray.app/email_images/logoicon.png', rendered)
        self.assertIn('class="content"', rendered)
        self.assertIn('class="footer"', rendered)
        self.assertNotIn('Compiled with Bootstrap Email', rendered)
