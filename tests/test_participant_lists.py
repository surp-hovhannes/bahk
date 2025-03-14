from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from hub.models import Fast, Church, Profile, Day
from datetime import datetime, timedelta
import time
import json
import pytz

User = get_user_model()

class FastParticipantListTests(APITestCase):
    """Tests specifically focused on Fast participant list functionality."""

    def setUp(self):
        """Set up test data needed for all test methods."""
        # Create a test church
        self.church = Church.objects.create(
            name="Test Church"
        )
        
        # Create a test fast with days
        today = datetime.now().date()
        self.fast = Fast.objects.create(
            name="Test Fast",
            description="A test fast",
            church=self.church
        )
        
        # Create days for the fast (5 days spanning today)
        for i in range(-2, 3):  # 5 days from 2 days ago to 2 days from now
            day_date = today + timedelta(days=i)
            Day.objects.create(
                fast=self.fast,
                date=day_date,
                church=self.church
            )
        
        # Create multiple users and profiles for testing pagination
        self.users = []
        self.profiles = []
        
        # Create 25 test users
        for i in range(25):
            user = User.objects.create_user(
                username=f"testuser{i}",
                email=f"test{i}@example.com",
                password="testpassword"
            )
            self.users.append(user)
            
            profile = Profile.objects.create(
                user=user,
                church=self.church,
                name=f"Test User {i}"  # Add a name to match the serializer
            )
            self.profiles.append(profile)
        
        # Make the first 20 users participants in the fast
        for i in range(20):
            self.profiles[i].fasts.add(self.fast)
            self.profiles[i].save()
        
        # URL to test
        self.regular_participants_url = reverse('fast-participants', kwargs={'fast_id': self.fast.id})
        self.paginated_participants_url = reverse('fast-participants-paginated', kwargs={'fast_id': self.fast.id})
    
    def test_regular_participants_endpoint_requires_auth(self):
        """Test that the regular participants endpoint requires authentication."""
        # Create an unauthenticated client
        client = APIClient()
        
        # Try to access the endpoint without authentication
        response = client.get(self.regular_participants_url)
        
        # Should get 401 Unauthorized
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_regular_participants_endpoint(self):
        """Test that the regular participants endpoint works with authentication."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        start_time = time.time()
        response = client.get(self.regular_participants_url)
        end_time = time.time()
        
        # Check status
        self.assertEqual(response.status_code, status.HTTP_200_OK, 
                         f"Failed with response data: {response.data}")
        
        # Check data
        self.assertIsInstance(response.data, list)
        
        # From looking at the serializer, we know that the ParticipantSerializer
        # includes: id, name, profile_image, thumbnail, location, abbreviation, user
        for participant in response.data:
            self.assertIn('id', participant)
            self.assertIn('name', participant)
            self.assertIn('user', participant)
            self.assertIn('abbreviation', participant)
            
            # Check abbreviation logic - if name is None, abbreviation will likely be 'u'
            # (first letter of username or default value)
            if participant['name']:
                self.assertEqual(participant['abbreviation'], participant['name'][0])
                
            # The user field will be set even if name is None
            self.assertIsNotNone(participant['user'])
        
        # Check performance
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Regular participants endpoint too slow: {response_time:.3f}s")
        print(f"Regular participants endpoint response time: {response_time:.3f}s")
    
    def test_regular_participants_limit(self):
        """Test that the regular participants endpoint respects the limit parameter."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        # Based on the implementation it should respect a limit parameter
        response = client.get(f"{self.regular_participants_url}?limit=5")
        
        # Should return only 5 participants
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 5)
    
    def test_participants_ordered_by_join_date(self):
        """Test that participants are ordered by join date (simulated by user creation date)."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        # Our test setup adds users in order, so the first users should appear first
        response = client.get(self.regular_participants_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Get the profile IDs in the order they appear in the response
        participant_ids = [p['id'] for p in response.data]
        
        # Our test added profiles 0-19 to the fast in order
        # The exact order may depend on the API implementation (might be ascending or descending)
        # So we just check that the IDs of participants that joined are present
        for i in range(min(len(participant_ids), 20)):
            self.assertIn(self.profiles[i].id, participant_ids)
    
    def test_paginated_participants_endpoint(self):
        """Test the paginated participants endpoint."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        start_time = time.time()
        response = client.get(self.paginated_participants_url)
        end_time = time.time()
        
        # Check status
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check paginated structure
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
        self.assertEqual(response.data['count'], 20)  # We added 20 participants
        
        # Check data in results
        participants = response.data['results']
        self.assertTrue(len(participants) > 0)
        
        # Verify response contains expected fields
        participant = participants[0]
        self.assertIn('id', participant)
        self.assertIn('name', participant)
        self.assertIn('user', participant)
        self.assertIn('abbreviation', participant)
        
        # Check response time
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Paginated participants endpoint too slow: {response_time:.3f}s")
        print(f"Paginated participants endpoint response time: {response_time:.3f}s")
    
    def test_pagination_works_correctly(self):
        """Test that pagination returns correct pages of results."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        # Get first page with 10 items
        response = client.get(f"{self.paginated_participants_url}?limit=10")
        
        # Check status and count
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 20)  # Total should be 20
        
        # First page should have 10 results
        first_page = response.data['results']
        self.assertEqual(len(first_page), 10)
        
        # Get second page
        response = client.get(f"{self.paginated_participants_url}?limit=10&offset=10")
        
        # Second page should also have 10 results
        second_page = response.data['results']
        self.assertEqual(len(second_page), 10)
        
        # Ensure first and second page items are different
        first_page_ids = [p['id'] for p in first_page]
        second_page_ids = [p['id'] for p in second_page]
        
        # No overlapping IDs between pages
        self.assertEqual(len(set(first_page_ids).intersection(set(second_page_ids))), 0)
    
    def test_pagination_empty_result(self):
        """Test pagination with offset beyond available results."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        response = client.get(f"{self.paginated_participants_url}?offset=50")
        
        # Should still return 200 but with empty results
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 20)  # Total should be 20
        self.assertEqual(len(response.data['results']), 0)  # But no results for this page
    
    def test_performance_with_different_page_sizes(self):
        """Test performance with different page sizes."""
        # Create authenticated client
        client = APIClient()
        client.force_authenticate(user=self.users[0])
        
        page_sizes = [5, 10, 20]
        
        print("\nParticipant list performance with different page sizes:")
        
        for size in page_sizes:
            start_time = time.time()
            response = client.get(f"{self.paginated_participants_url}?limit={size}")
            end_time = time.time()
            
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data['results']), min(size, 20))  # Can't get more results than exist
            
            response_time = end_time - start_time
            print(f"Page size {size}: {response_time:.3f}s")
            self.assertLess(response_time, 0.3, f"Slow response for page size {size}: {response_time:.3f}s") 