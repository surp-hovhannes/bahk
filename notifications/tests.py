from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock
from .models import DeviceToken
from .views import DeviceTokenCreateView, TestPushNotificationView
import json

class DeviceTokenTests(APITestCase):
    def setUp(self):
        # Create a test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Set up the client
        self.client = APIClient()
        
        # Define valid token data
        self.valid_token_data = {
            'token': 'ExponentPushToken[xxxxxxxxxxxxxxxxxxxx]',
            'device_type': 'ios',
            'user': self.user.id
        }
        
        # Define test URLs with the correct URL names
        self.create_token_url = reverse('register-device-token')
        self.test_push_url = reverse('test-push-notification')
    
    def test_create_token(self):
        """Test creating a new device token"""
        response = self.client.post(
            self.create_token_url,
            self.valid_token_data,
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(DeviceToken.objects.count(), 1)
        self.assertEqual(DeviceToken.objects.get().token, self.valid_token_data['token'])
    
    def test_update_existing_token(self):
        """Test updating an existing token (upsert behavior)"""
        # First create a token
        DeviceToken.objects.create(
            token=self.valid_token_data['token'],
            device_type='android',  # Different from what we'll update to
            user=self.user
        )
        
        # Now try to create the same token but with different device_type
        update_data = self.valid_token_data.copy()
        update_data['device_type'] = 'ios'
        
        response = self.client.post(
            self.create_token_url,
            update_data,
            format='json'
        )
        
        # Should be a 200 OK response, not 201 Created
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Should still be only one token
        self.assertEqual(DeviceToken.objects.count(), 1)
        
        # Device type should be updated
        token = DeviceToken.objects.get()
        self.assertEqual(token.device_type, 'ios')
    
    def test_create_token_invalid_data(self):
        """Test creating a token with invalid data"""
        invalid_data = {
            'token': 'invalid-token-format',  # Should be in the format ExponentPushToken[xxx]
            'device_type': 'ios',
            'user': self.user.id
        }
        
        response = self.client.post(
            self.create_token_url,
            invalid_data,
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DeviceToken.objects.count(), 0)
    
    def test_create_token_invalid_device_type(self):
        """Test creating a token with invalid device type"""
        invalid_type_data = self.valid_token_data.copy()
        invalid_type_data['device_type'] = 'windows'  # Not in the valid choices
        
        response = self.client.post(
            self.create_token_url,
            invalid_type_data,
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DeviceToken.objects.count(), 0)


class TestPushNotificationTests(APITestCase):
    def setUp(self):
        # Create a test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create a test token
        self.token = DeviceToken.objects.create(
            token='ExponentPushToken[xxxxxxxxxxxxxxxxxxxx]',
            device_type='ios',
            user=self.user,
            is_active=True
        )
        
        # Set up the client
        self.client = APIClient()
        
        # Define test URL with the correct URL name
        self.test_push_url = reverse('test-push-notification')
    
    @patch('notifications.views.send_push_notification')
    def test_push_notification_success(self, mock_send_push):
        """Test sending a push notification successfully"""
        # Configure the mock to return a success response
        mock_send_push.return_value = {
            'success': True,
            'sent': 1,
            'failed': 0,
            'invalid_tokens': [],
            'errors': []
        }
        
        data = {
            'token': self.token.token,
            'message': 'Test message'
        }
        
        response = self.client.post(
            self.test_push_url,
            data,
            format='json'
        )
        
        # Verify the response and that the mock was called correctly
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        mock_send_push.assert_called_once_with(
            message='Test message',
            tokens=[self.token.token]
        )
    
    @patch('notifications.views.send_push_notification')
    def test_push_notification_no_token(self, mock_send_push):
        """Test sending a push notification without a token"""
        # The mock should not be called in this test
        
        data = {
            'message': 'Test message'
            # No token provided
        }
        
        response = self.client.post(
            self.test_push_url,
            data,
            format='json'
        )
        
        # Verify the response and that the mock was not called
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'Token is required')
        mock_send_push.assert_not_called()
    
    @patch('notifications.views.send_push_notification')
    def test_push_notification_default_message(self, mock_send_push):
        """Test sending a push notification with default message"""
        # Configure the mock to return a success response
        mock_send_push.return_value = {
            'success': True,
            'sent': 1,
            'failed': 0,
            'invalid_tokens': [],
            'errors': []
        }
        
        data = {
            'token': self.token.token
            # No message provided, should use default
        }
        
        response = self.client.post(
            self.test_push_url,
            data,
            format='json'
        )
        
        # Verify the response and that the mock was called with the default message
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send_push.assert_called_once_with(
            message='Test notification',  # Default message from the view
            tokens=[self.token.token]
        )
