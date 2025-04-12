from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse
from notifications.models import PromoEmail
from django.conf import settings
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.core.files.storage import FileSystemStorage

class TestStaticFilesStorage(FileSystemStorage):
    """Mock storage that returns a dummy URL for static files."""
    def url(self, name):
        return f'http://testserver/static/{name}'

@override_settings(
    SITE_URL='http://testserver',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com',
    STATICFILES_STORAGE='notifications.tests.test_email_templates.TestStaticFilesStorage',
    STATIC_URL='/static/'
)
class PromoEmailTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create a test promo email
        self.promo = PromoEmail.objects.create(
            title='Test Promo',
            subject='Test Subject',
            content_html='<p>Test content</p>',
            content_text='Test content'
        )
        
        # Generate unsubscribe token
        from django.core.signing import TimestampSigner
        signer = TimestampSigner()
        unsubscribe_token = signer.sign(self.user.id)
        self.unsubscribe_url = f"{settings.SITE_URL}{reverse('notifications:unsubscribe')}?token={unsubscribe_token}"
        
        # Add site_url to context
        self.base_context = {
            'user': self.user,
            'site_url': settings.SITE_URL,
            'unsubscribe_url': self.unsubscribe_url
        }
    
    def test_promotional_email_template_renders(self):
        """Test that the promotional email template renders correctly."""
        context = {
            **self.base_context,
            'title': self.title,
            'email_content': self.promo.content_html,
        }
        
        rendered = render_to_string('email/promotional_email.html', context)
        
        self.assertIn(self.promo.content_html, rendered)
        self.assertIn(self.unsubscribe_url, rendered)
        self.assertIn('http://testserver/app-icon-1024.png', rendered)
    
    def test_promotional_email_template_with_complex_content(self):
        """Test that the promotional email template handles complex HTML content."""
        complex_html = """
        <div class="container">
            <h1>Complex Title</h1>
            <p>Some text with <strong>bold</strong> and <em>italic</em></p>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
            </ul>
        </div>
        """
        
        context = {
            **self.base_context,
            'email_content': complex_html,
        }
        
        rendered = render_to_string('email/promotional_email.html', context)
        
        self.assertIn(complex_html, rendered)
        self.assertIn(self.unsubscribe_url, rendered)
        self.assertIn('http://testserver/app-icon-1024.png', rendered)
    
    def test_promotional_email_template_with_special_characters(self):
        """Test that the promotional email template handles special characters."""
        special_content = """
        <p>Special characters: áéíóú ñ & < > " '</p>
        """
        
        context = {
            **self.base_context,
            'email_content': special_content,
        }
        
        rendered = render_to_string('email/promotional_email.html', context)
        
        self.assertIn(special_content, rendered)
        self.assertIn(self.unsubscribe_url, rendered)
        self.assertIn('http://testserver/app-icon-1024.png', rendered)
    
    def test_promotional_email_template_with_missing_context(self):
        """Test that the promotional email template handles missing context variables."""
        context = {
            'unsubscribe_url': self.unsubscribe_url,
            'site_url': settings.SITE_URL
        }
        
        rendered = render_to_string('email/promotional_email.html', context)
        
        self.assertIn(self.unsubscribe_url, rendered)
        self.assertNotIn('None', rendered)
        self.assertIn('http://testserver/app-icon-1024.png', rendered) 