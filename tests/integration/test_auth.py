from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.models import User
from hub.models import Church, Profile
from tests.fixtures.test_data import TestDataFactory
import datetime

class AuthenticationTest(APITestCase):
    def setUp(self):
        # Create a church for testing using factory
        self.church = TestDataFactory.create_church()
        
        # Create a user for testing using factory
        self.user = TestDataFactory.create_user()
        
        # Create a profile for the user using factory
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church
        )

    def test_create_token(self):
        """
        Ensure we can receive a token for a given user.
        """
        # Create a test user using factory (automatically email-compatible)
        user = TestDataFactory.create_user(password='testpass123')
        
        # Create a profile for the user using factory
        TestDataFactory.create_profile(user=user, church=self.church)
        
        # Get token using email as username (EmailBackend expects this)
        response = self.client.post(
            reverse('token_obtain_pair'),
            {
                'username': user.email,  # EmailBackend treats username as email
                'password': 'testpass123'
            },
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_access_protected_view(self):
        """
        Ensure that a protected view is accessible with a valid token.
        """
        # Obtain a token
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        # Try to access a protected view
        protected_url = reverse('fast_on_date')
        response = self.client.get(protected_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_no_access_with_invalid_token(self):
        """
        Ensure that a protected view cannot be accessed with an invalid token.
        """
        # Use an invalid token
        self.client.credentials(HTTP_AUTHORIZATION='Bearer wrongtoken123')
        
        # Try to access a protected view
        protected_url = reverse('fast_on_date')
        response = self.client.get(protected_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)