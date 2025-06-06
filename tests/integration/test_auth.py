from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.models import User

class AuthenticationTest(APITestCase):
    def setUp(self):
        # Create a user for testing
        self.user = User.objects.create_user(username='testuser', password='testpassword123')

    def test_create_token(self):
        """
        Ensure we can receive a token for a given user.
        """
        url = reverse('token_obtain_pair')
        data = {
            'username': 'testuser',
            'password': 'testpassword123',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue('access' in response.data)
        self.assertTrue('refresh' in response.data)

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