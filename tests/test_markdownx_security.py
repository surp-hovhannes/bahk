from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class MarkdownXSecurityTests(TestCase):
    def test_anonymous_users_cannot_access_markdownx_upload(self):
        response = self.client.post(reverse('markdownx_upload'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_anonymous_users_cannot_access_markdownx_markdownify(self):
        response = self.client.post(reverse('markdownx_markdownify'), {'content': '**test**'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_users_can_access_markdownx_markdownify(self):
        staff_user = User.objects.create_user(
            username='editor@example.com',
            email='editor@example.com',
            password='testpass123',
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.post(reverse('markdownx_markdownify'), {'content': '**test**'})

        self.assertNotEqual(response.status_code, 302)
