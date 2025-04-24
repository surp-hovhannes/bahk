from django.test import TestCase, Client, override_settings, modify_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from django.contrib.auth.models import Permission, Group
from django.contrib.contenttypes.models import ContentType
from unittest.mock import patch, MagicMock
import datetime

from notifications.models import PromoEmail
from hub.models import Church, Fast, Profile

User = get_user_model()

@override_settings(
    FRONTEND_URL='http://testserver',
    BACKEND_URL='http://testserver',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com'
)
@modify_settings(
    MIDDLEWARE={
        'append': 'django.contrib.sessions.middleware.SessionMiddleware',
        'append': 'django.contrib.auth.middleware.AuthenticationMiddleware',
    }
)
class PromoEmailAdminTests(TestCase):
    """Tests for the promotional email admin views."""

    def setUp(self):
        """Set up test data."""
        # Create admin user
        self.admin_user = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='adminpass123'
        )
        
        # Create regular user
        self.regular_user = User.objects.create_user(
            username='regular',
            email='regular@example.com',
            password='regularpass123'
        )
        
        # Create church
        self.church = Church.objects.create(name="Test Church")
        
        # Create fast
        self.fast = Fast.objects.create(
            name="Test Fast",
            church=self.church,
            description="A test fast"
        )
        
        # Create user profiles
        self.admin_profile = Profile.objects.create(
            user=self.admin_user,
            church=self.church,
            receive_promotional_emails=True
        )
        self.admin_profile.fasts.add(self.fast)
        
        self.regular_profile = Profile.objects.create(
            user=self.regular_user,
            church=self.church,
            receive_promotional_emails=True
        )
        self.regular_profile.fasts.add(self.fast)
        
        # Create promotional email
        self.promo = PromoEmail.objects.create(
            title="Test Promo",
            subject="Test Subject",
            content_html="<p>Test content</p>",
            content_text="Test content",
            all_users=True
        )
        
        # Create client
        self.client = Client()
        
        # Ensure admin user has staff status and superuser status
        self.admin_user.is_staff = True
        self.admin_user.is_superuser = True
        self.admin_user.save()
        
        # Add all permissions for PromoEmail model to admin user
        content_type = ContentType.objects.get_for_model(PromoEmail)
        permissions = Permission.objects.filter(content_type=content_type)
        for permission in permissions:
            self.admin_user.user_permissions.add(permission)
        
        # Add view and change permissions explicitly
        view_permission = Permission.objects.get(
            content_type=content_type,
            codename='view_promoemail'
        )
        change_permission = Permission.objects.get(
            content_type=content_type,
            codename='change_promoemail'
        )
        self.admin_user.user_permissions.add(view_permission)
        self.admin_user.user_permissions.add(change_permission)
        
        # Add admin user to staff group
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.admin_user.groups.add(staff_group)
        
        # Add admin permissions
        admin_content_type = ContentType.objects.get_for_model(PromoEmail)
        admin_permissions = Permission.objects.filter(content_type=admin_content_type)
        for permission in admin_permissions:
            self.admin_user.user_permissions.add(permission)
        
        # Simple login - Django's test client handles sessions properly
        login_successful = self.client.login(username='admin@example.com', password='adminpass123')
        self.assertTrue(login_successful, "Login failed")

    def test_admin_list_view_requires_login(self):
        """Test that the admin list view requires login."""
        self.client.logout()
        response = self.client.get(reverse('admin:notifications_promoemail_changelist'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_admin_list_view_requires_staff(self):
        """Test that the admin list view requires staff status."""
        self.client.logout()
        self.client.login(username='regular', password='regularpass123')
        response = self.client.get(reverse('admin:notifications_promoemail_changelist'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_admin_list_view_accessible_to_staff(self):
        """Test that the admin list view is accessible to staff."""
        response = self.client.get(reverse('admin:notifications_promoemail_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Promo")

    def test_admin_detail_view_accessible_to_staff(self):
        """Test that the admin detail view is accessible to staff."""
        response = self.client.get(
            reverse('admin:notifications_promoemail_change', args=[self.promo.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Promo")
        self.assertContains(response, "Test Subject")
        self.assertContains(response, "Test content")

    @patch('notifications.models.PromoEmail.send_preview')
    def test_admin_send_preview_view_accessible_to_staff(self, mock_send_preview):
        """Test that the admin send preview view is accessible to staff."""
        mock_send_preview.return_value = {'success': True}
        response = self.client.get(
            reverse('admin:notifications_promoemail_send_preview', args=[self.promo.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send Preview")

    @patch('notifications.models.PromoEmail.send_preview')
    def test_admin_send_preview_view_sends_email(self, mock_send_preview):
        """Test that the admin send preview view sends an email."""
        mock_send_preview.return_value = {'success': True, 'to': 'test@example.com'}
        response = self.client.post(
            reverse('admin:notifications_promoemail_send_preview', args=[self.promo.id]),
            {'email': 'test@example.com'}
        )
        self.assertEqual(response.status_code, 302)
        mock_send_preview.assert_called_once_with('test@example.com')

    @patch('notifications.models.PromoEmail.send_preview')
    def test_admin_send_preview_view_handles_errors(self, mock_send_preview):
        """Test that the admin send preview view handles errors."""
        mock_send_preview.return_value = {'success': False, 'error': 'Test error'}
        response = self.client.post(
            reverse('admin:notifications_promoemail_send_preview', args=[self.promo.id]),
            {'email': 'test@example.com'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test error")

    def test_admin_view_rendered_view_accessible_to_staff(self):
        """Test that the admin view rendered view is accessible to staff."""
        response = self.client.get(
            reverse('admin:notifications_promoemail_view_rendered', args=[self.promo.id])
        )
        # Add these debug lines to see where it's redirecting
        print(f"Response status: {response.status_code}")
        print(f"Response redirect chain: {response.url if response.status_code == 302 else 'No redirect'}")
        
        # Follow redirects
        response = self.client.get(
            reverse('admin:notifications_promoemail_view_rendered', args=[self.promo.id]),
            follow=True
        )
        print(f"After follow, status: {response.status_code}")
        print(f"Content: {response.content[:200]}")  # First 200 chars of response
        
        self.assertEqual(response.status_code, 200)
        
        # Check basic content
        self.assertContains(response, "Email Details")
        self.assertContains(response, "Test Promo")
        self.assertContains(response, "Test Subject")
        
        # Check that the email template is used
        self.assertContains(response, "unsubscribe")
        self.assertContains(response, "http://testserver")  # SITE_URL from settings
        self.assertContains(response, "[PREVIEW]")  # Preview marker
        
        # Check that the rendered email is in the preview container
        self.assertContains(response, "email-preview-container")
        self.assertContains(response, "Test content")

    @patch('notifications.tasks.send_promo_email_task.delay')
    def test_admin_send_now_action_sends_email(self, mock_send_task):
        """Test that the admin send now action sends an email."""
        response = self.client.post(
            reverse('admin:notifications_promoemail_changelist'),
            {
                'action': 'send_now',
                '_selected_action': [self.promo.id],
            }
        )
        self.assertRedirects(response, reverse('admin:notifications_promoemail_changelist'))
        mock_send_task.assert_called_once_with(self.promo.id)

    @patch('notifications.tasks.send_promo_email_task.delay')
    def test_admin_schedule_view_accessible_to_staff(self, mock_send_task):
        """Test that the admin schedule view is accessible to staff."""
        response = self.client.get(
            reverse('admin:notifications_promoemail_schedule', args=[self.promo.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule email")

    @patch('notifications.tasks.send_promo_email_task.delay')
    def test_admin_schedule_view_schedules_email(self, mock_send_task):
        """Test that the admin schedule view schedules an email."""
        schedule_time = timezone.now() + datetime.timedelta(hours=1)
        response = self.client.post(
            reverse('admin:notifications_promoemail_schedule', args=[self.promo.id]),
            {'scheduled_for': schedule_time.strftime('%Y-%m-%dT%H:%M')}
        )
        self.assertEqual(response.status_code, 302)
        
        # Refresh promo from db
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.status, PromoEmail.SCHEDULED)
        self.assertIsNotNone(self.promo.scheduled_for)

    @patch('notifications.tasks.send_promo_email_task.delay')
    def test_admin_schedule_view_handles_invalid_date(self, mock_send_task):
        """Test that the admin schedule view handles invalid date format."""
        response = self.client.post(
            reverse('admin:notifications_promoemail_schedule', args=[self.promo.id]),
            {'scheduled_for': 'invalid-date'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid date format")
        
        # Refresh promo from db
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.status, PromoEmail.DRAFT)
        self.assertIsNone(self.promo.scheduled_for)

    def test_admin_recipient_count_display(self):
        """Test that the admin displays the correct recipient count."""
        # Set up promo with specific filters
        self.promo.all_users = False
        self.promo.church_filter = self.church
        self.promo.save()
        
        response = self.client.get(reverse('admin:notifications_promoemail_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2")  # Both users are in the church

    def test_admin_recipient_count_with_unsubscribe_filter(self):
        """Test that the admin displays the correct recipient count with unsubscribe filter."""
        # Set up promo with unsubscribe filter
        self.promo.all_users = True
        self.promo.exclude_unsubscribed = True
        self.promo.save()
        
        # Set one user to not receive promotional emails
        self.regular_profile.receive_promotional_emails = False
        self.regular_profile.save()
        
        response = self.client.get(reverse('admin:notifications_promoemail_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1")  # Only admin user receives emails

    def test_admin_recipient_count_with_fast_filter(self):
        """Test that the admin displays the correct recipient count with fast filter."""
        # Set up promo with fast filter
        self.promo.all_users = False
        self.promo.joined_fast = self.fast
        self.promo.save()
        
        response = self.client.get(reverse('admin:notifications_promoemail_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2")  # Both users are in the fast 

    def test_admin_replicate_action_replicates_selected_promos(self):
        """Test that the admin replicate action creates copies of selected promos."""
        original_count = PromoEmail.objects.count()
        response = self.client.post(
            reverse('admin:notifications_promoemail_changelist'),
            {
                'action': 'replicate_promo_emails',
                '_selected_action': [self.promo.id],
            }
        )
        self.assertRedirects(response, reverse('admin:notifications_promoemail_changelist'))
        # One new promo created
        self.assertEqual(PromoEmail.objects.count(), original_count + 1)
        # Verify new promo attributes
        new_promo = PromoEmail.objects.exclude(pk=self.promo.pk).first()
        self.assertEqual(new_promo.title, f"Copy of {self.promo.title}")
        self.assertEqual(new_promo.subject, self.promo.subject)
        self.assertEqual(new_promo.content_html, self.promo.content_html)
        self.assertEqual(new_promo.content_text, self.promo.content_text)
        self.assertEqual(new_promo.all_users, self.promo.all_users)
        self.assertEqual(new_promo.church_filter, self.promo.church_filter)
        self.assertEqual(new_promo.joined_fast, self.promo.joined_fast)
        self.assertEqual(new_promo.exclude_unsubscribed, self.promo.exclude_unsubscribed)
        # Selected users should be replicated
        self.assertEqual(
            list(new_promo.selected_users.values_list('id', flat=True)),
            list(self.promo.selected_users.values_list('id', flat=True))
        ) 