from django.test import TestCase, Client
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

class FastEndpointTests(APITestCase):
    """Test the core fast endpoints for functionality and performance."""
    
    def setUp(self):
        """Set up test data needed for all test methods."""
        # Create a test church (only has name field)
        self.church = Church.objects.create(
            name="Test Church"
        )
        
        # Create test users
        self.user1 = User.objects.create_user(
            username="testuser1",
            email="test1@example.com",
            password="testpassword"
        )
        self.user2 = User.objects.create_user(
            username="testuser2", 
            email="test2@example.com", 
            password="testpassword"
        )
        
        # Create profiles for the users explicitly
        self.profile1 = Profile.objects.create(
            user=self.user1,
            church=self.church
        )
        
        self.profile2 = Profile.objects.create(
            user=self.user2,
            church=self.church
        )
        
        # Create a test fast with days
        today = datetime.now().date()
        self.fast = Fast.objects.create(
            name="Test Fast",
            description="A test fast",
            church=self.church
        )
        
        # Create days for the fast (past, present, future)
        # The Day model only has date, fast, and church fields
        for i in range(-2, 3):  # 5 days from 2 days ago to 2 days from now
            day_date = today + timedelta(days=i)
            Day.objects.create(
                fast=self.fast,
                date=day_date,
                church=self.church  # Add church reference
            )
        
        # Create client for authenticated requests
        self.client = APIClient()
        
        # URLs we'll test
        self.fasts_list_url = reverse('fast-list')
        self.fast_detail_url = reverse('fast-detail', kwargs={'pk': self.fast.id})
        self.fast_by_date_url = reverse('fast-by-date')
        self.fast_join_url = reverse('fast-join')
        self.fast_leave_url = reverse('leave-fast')
        self.fast_participants_url = reverse('fast-participants', kwargs={'fast_id': self.fast.id})
        self.fast_stats_url = reverse('fast-stats')
    
    def test_fast_list_returns_200(self):
        """Test that the fast listing endpoint returns 200 OK."""
        # Add church_id as required parameter
        url = f"{self.fasts_list_url}?church_id={self.church.id}"
        
        # Get response time for unauthenticated request
        start_time = time.time()
        response = self.client.get(url)
        end_time = time.time()
        
        # Check response status
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify response structure
        self.assertIsInstance(response.data, list)
        self.assertTrue(len(response.data) > 0)
        self.assertEqual(response.data[0]['name'], self.fast.name)
        
        # Check response time (should be under 300ms)
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Fast list endpoint too slow: {response_time:.3f}s")
        
        # Log the performance for review
        print(f"Fast list endpoint response time: {response_time:.3f}s")
    
    def test_fast_detail_returns_correct_data(self):
        """Test that the fast detail endpoint returns correct data."""
        start_time = time.time()
        response = self.client.get(self.fast_detail_url)
        end_time = time.time()
        
        # Check status
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify data content - adjust to match actual response structure
        self.assertEqual(response.data['id'], self.fast.id)
        self.assertEqual(response.data['name'], self.fast.name)
        self.assertEqual(response.data['description'], self.fast.description)
        # Check for expected fields based on the actual response
        self.assertIn('start_date', response.data)
        self.assertIn('end_date', response.data)
        self.assertIn('total_number_of_days', response.data)
        self.assertEqual(response.data['total_number_of_days'], 5)  # 5 days we created
        
        # Check response time (should be under 300ms)
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Fast detail endpoint too slow: {response_time:.3f}s")
        print(f"Fast detail endpoint response time: {response_time:.3f}s")
    
    def test_fast_by_date_returns_correct_data(self):
        """Test that the fast-by-date endpoint returns appropriate fasts."""
        today = datetime.now().date()
        # Add church_id parameter which is required
        url = f"{self.fast_by_date_url}?date={today.isoformat()}&church_id={self.church.id}"
        
        start_time = time.time()
        response = self.client.get(url)
        end_time = time.time()
        
        # Check status
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # The response is paginated with 'results' containing the list of fasts
        self.assertIn('results', response.data)
        self.assertIsInstance(response.data['results'], list)
        self.assertTrue(len(response.data['results']) > 0)
        
        # Our test fast should be in the results list since it spans today
        found = False
        for fast in response.data['results']:
            if fast['id'] == self.fast.id:
                found = True
                break
        self.assertTrue(found, "Expected fast not found in response")
        
        # Check response time
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Fast by date endpoint too slow: {response_time:.3f}s")
        print(f"Fast by date endpoint response time: {response_time:.3f}s")
    
    def test_join_and_leave_fast(self):
        """Test joining and leaving a fast."""
        self.client.force_authenticate(user=self.user1)
        
        # Join the fast
        join_data = {"fast_id": self.fast.id}
        start_time = time.time()
        join_response = self.client.put(self.fast_join_url, join_data)
        join_time = time.time() - start_time
        
        # Check status and that the fast was joined
        self.assertEqual(join_response.status_code, status.HTTP_200_OK)
        self.profile1.refresh_from_db()
        self.assertIn(self.fast, self.profile1.fasts.all())
        self.assertLess(join_time, 0.5, f"Join fast endpoint too slow: {join_time:.3f}s")
        
        # Leave the fast
        leave_data = {"fast_id": self.fast.id}
        start_time = time.time()
        leave_response = self.client.put(self.fast_leave_url, leave_data)
        leave_time = time.time() - start_time
        
        # Check status and that the fast was left
        self.assertEqual(leave_response.status_code, status.HTTP_200_OK)
        self.profile1.refresh_from_db()
        self.assertNotIn(self.fast, self.profile1.fasts.all())
        self.assertLess(leave_time, 0.5, f"Leave fast endpoint too slow: {leave_time:.3f}s")
        
        print(f"Join fast endpoint response time: {join_time:.3f}s")
        print(f"Leave fast endpoint response time: {leave_time:.3f}s")
    
    def test_fast_participants_endpoint(self):
        """Test the fast participants endpoint."""
        # First join the fast with user1
        self.profile1.fasts.add(self.fast)
        self.profile1.save()
        
        # Authenticate as user2
        self.client.force_authenticate(user=self.user2)
        
        # Get participants
        start_time = time.time()
        response = self.client.get(self.fast_participants_url)
        end_time = time.time()
        
        # Check status and data
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)  # Should have 1 participant (user1)
        
        # Based on the observed response structure, check specific fields
        participant = response.data[0]
        self.assertIn('id', participant)
        self.assertIn('user', participant)  # The username value is in the 'user' field
        self.assertEqual(participant['id'], self.profile1.id)
        
        # Check response time
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Fast participants endpoint too slow: {response_time:.3f}s")
        print(f"Fast participants endpoint response time: {response_time:.3f}s")
    
    def test_fast_stats_endpoint(self):
        """Test the fast stats endpoint."""
        # First join the fast with user1
        self.profile1.fasts.add(self.fast)
        self.profile1.save()
        
        # Authenticate as user1
        self.client.force_authenticate(user=self.user1)
        
        # Get stats
        start_time = time.time()
        response = self.client.get(self.fast_stats_url)
        end_time = time.time()
        
        # Check status and data - adjust fields to match actual response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Based on the error, the response structure is different
        # The API returns 'joined_fasts' instead of 'fast_ids'
        self.assertIn('joined_fasts', response.data)
        self.assertIn('total_fasts', response.data)
        self.assertIn('total_fast_days', response.data)  # Field name is different
        self.assertEqual(response.data['total_fasts'], 1)
        
        # Check response time
        response_time = end_time - start_time
        self.assertLess(response_time, 0.3, f"Fast stats endpoint too slow: {response_time:.3f}s")
        print(f"Fast stats endpoint response time: {response_time:.3f}s")
    
    def test_endpoints_under_load(self):
        """Test endpoints performance under sequential requests."""
        num_requests = 5
        # Add required query parameters to URLs
        endpoints = [
            f"{self.fasts_list_url}?church_id={self.church.id}",
            self.fast_detail_url,
            f"{self.fast_by_date_url}?date={datetime.now().date().isoformat()}&church_id={self.church.id}"
        ]
        
        print("\nEndpoint performance under load:")
        
        for endpoint in endpoints:
            total_time = 0
            for i in range(num_requests):
                start_time = time.time()
                response = self.client.get(endpoint)
                request_time = time.time() - start_time
                total_time += request_time
                
                self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            avg_time = total_time / num_requests
            print(f"Endpoint {endpoint}: avg response time over {num_requests} requests: {avg_time:.3f}s")
            self.assertLess(avg_time, 0.3, f"Endpoint {endpoint} too slow under load: {avg_time:.3f}s") 