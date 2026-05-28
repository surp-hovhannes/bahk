import re

from django.core import mail
from django.contrib.auth import authenticate
from django.test import override_settings
from django.urls import resolve
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import default_token_generator
from rest_framework.test import APITestCase
from rest_framework import status
from tests.fixtures.test_data import TestDataFactory

from hub.serializers import PasswordResetSerializer, PasswordResetConfirmSerializer
from hub.views.user import PasswordResetConfirmView, PasswordResetView


@override_settings(
    APP_URL='https://app.test',
    FRONTEND_URL='https://frontend.test',
    EMAIL_HOST_USER='help@example.com',
)
class PasswordResetTests(APITestCase):
    def setUp(self):
        # Use TestDataFactory for email-compatible user creation
        self.user = TestDataFactory.create_user(
            username='test@example.com',
            email='test@example.com',
            password='oldpassword123'
        )
        self.password_reset_url = '/api/password/reset/'
        self.password_reset_confirm_url = '/api/password/reset/confirm/'

    def test_password_reset_routes_are_mounted_under_api(self):
        reset_match = resolve('/api/password/reset/')
        confirm_match = resolve('/api/password/reset/confirm/')

        self.assertEqual(reset_match.func.view_class, PasswordResetView)
        self.assertEqual(confirm_match.func.view_class, PasswordResetConfirmView)

    def test_password_reset_serializer_valid_email(self):
        serializer = PasswordResetSerializer(data={'email': 'test@example.com'})
        self.assertTrue(serializer.is_valid())

    def test_password_reset_serializer_invalid_email(self):
        serializer = PasswordResetSerializer(data={'email': 'nonexistent@example.com'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('email', serializer.errors)

    def test_password_reset_confirm_serializer_passwords_match(self):
        # Generate valid token and uidb64
        token = default_token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        
        data = {
            'token': token,
            'uidb64': uidb64,
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }
        serializer = PasswordResetConfirmSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_password_reset_confirm_serializer_passwords_dont_match(self):
        token = default_token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        
        data = {
            'token': token,
            'uidb64': uidb64,
            'new_password': 'newpassword123',
            'confirm_password': 'differentpassword123'
        }
        serializer = PasswordResetConfirmSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)

    def test_password_reset_endpoint(self):
        response = self.client.post(self.password_reset_url, {'email': 'test@example.com'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'detail': 'Password reset email has been sent.'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('test@example.com', mail.outbox[0].to)
        self.assertEqual(mail.outbox[0].subject, 'Password Reset Request')

    def test_password_reset_endpoint_invalid_email(self):
        response = self.client.post(self.password_reset_url, {'email': 'nonexistent@example.com'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('email', response.data)

    def test_password_reset_endpoint_rejects_malformed_input(self):
        response = self.client.post(self.password_reset_url, {'email': 'not-an-email'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('email', response.data)

    def test_password_reset_email_contains_confirmable_link(self):
        reset_response = self.client.post(
            self.password_reset_url,
            {'email': 'test@example.com'},
        )

        self.assertEqual(reset_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        html_body = mail.outbox[0].alternatives[0][0]
        match = re.search(
            r'https://app\.test/reset-password/(?P<uidb64>[^/]+)/(?P<token>[^"\s<]+)',
            html_body,
        )
        self.assertIsNotNone(match)

        confirm_response = self.client.post(
            self.password_reset_confirm_url,
            {
                'uidb64': match.group('uidb64'),
                'token': match.group('token'),
                'new_password': 'newpassword123',
                'confirm_password': 'newpassword123',
            },
        )

        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword123'))

    def test_password_reset_confirm_endpoint(self):
        # Generate valid token and uidb64
        token = default_token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        
        data = {
            'token': token,
            'uidb64': uidb64,
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }
        
        response = self.client.post(self.password_reset_confirm_url, data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'detail': 'Password has been reset successfully.'})
        
        # Verify the password was actually changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword123'))
        self.assertEqual(
            authenticate(username='test@example.com', password='newpassword123'),
            self.user,
        )

    def test_password_reset_confirm_endpoint_invalid_token(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        
        data = {
            'token': 'invalid-token',
            'uidb64': uidb64,
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }
        
        response = self.client.post(self.password_reset_confirm_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Verify the password was not changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpassword123'))

    def test_password_reset_confirm_endpoint_invalid_uidb64(self):
        token = default_token_generator.make_token(self.user)
        
        data = {
            'token': token,
            'uidb64': 'invalid-uidb64',
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }
        
        response = self.client.post(self.password_reset_confirm_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Verify the password was not changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpassword123')) 

    def test_password_reset_confirm_endpoint_rejects_stale_token(self):
        token = default_token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.user.set_password('intermediatepassword123')
        self.user.save()

        data = {
            'token': token,
            'uidb64': uidb64,
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }

        response = self.client.post(self.password_reset_confirm_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('non_field_errors', response.data)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('intermediatepassword123'))

    def test_password_reset_confirm_endpoint_rejects_malformed_payload(self):
        response = self.client.post(self.password_reset_confirm_url, {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            set(response.data.keys()),
            {'token', 'uidb64', 'new_password', 'confirm_password'},
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpassword123'))

    def test_password_reset_confirm_endpoint_passwords_dont_match(self):
        token = default_token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        
        data = {
            'token': token,
            'uidb64': uidb64,
            'new_password': 'newpassword123',
            'confirm_password': 'differentpassword123'
        }
        
        response = self.client.post(self.password_reset_confirm_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Verify the password was not changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpassword123')) 
