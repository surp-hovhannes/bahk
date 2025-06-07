from django.test import TestCase, Client
from django.test.utils import tag
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from hub.models import Fast, Church, Profile, Day
from tests.fixtures.test_data import TestDataFactory
from datetime import datetime, timedelta
import time
import json
import pytz

User = get_user_model()

class FastEndpointTests(APITestCase):
    """Test the core fast endpoints for functionality and performance."""
    
    def setUp(self):
        """Set up test data needed for all test methods."""
        # Create a test church using TestDataFactory
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create test users using TestDataFactory (email-compatible)
        self.user1 = TestDataFactory.create_user(
            username="testuser1@example.com",
            email="test1@example.com",
            password="testpassword"
        )
        self.user2 = TestDataFactory.create_user(
            username="testuser2@example.com", 
            email="test2@example.com", 
            password="testpassword"
        )
        
        # Create profiles for the users using TestDataFactory
        self.profile1 = TestDataFactory.create_profile(
            user=self.user1,
            church=self.church
        )
        
        self.profile2 = TestDataFactory.create_profile(
            user=self.user2,
            church=self.church
        )
        
        # Create a test fast with days using TestDataFactory
        today = datetime.now().date()
        self.fast = TestDataFactory.create_fast(
            name="Test Fast",
            church=self.church,
            description="A test fast"
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
    
    @tag('performance')
    def test_fast_list_returns_200(self):
        """Test that the fast listing endpoint returns 200 OK."""
        start_time = time.time()
        response = self.client.get(self.fasts_list_url + f'?church_id={self.church.id}')
        response_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.json(), list)  # Returns a list, not dict
        print(f"Fast list endpoint response time: {response_time:.3f}s")
    
    @tag('performance')
    def test_fast_detail_returns_correct_data(self):
        """Test that the fast detail endpoint returns correct data."""
        start_time = time.time()
        response = self.client.get(self.fast_detail_url)
        response_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['name'], self.fast.name)
        self.assertEqual(data['id'], self.fast.id)
        print(f"Fast detail endpoint response time: {response_time:.3f}s")
    
    @tag('performance')
    def test_fast_by_date_returns_correct_data(self):
        """Test that the fast-by-date endpoint returns appropriate fasts."""
        today = datetime.now().date().strftime('%Y-%m-%d')
        start_time = time.time()
        response = self.client.get(f"{self.fast_by_date_url}?date={today}&church_id={self.church.id}")
        response_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsInstance(data, dict)
        print(f"Fast by date endpoint response time: {response_time:.3f}s")
    
    @tag('performance')
    def test_join_and_leave_fast(self):
        """Test joining and leaving a fast."""
        self.client.force_authenticate(user=self.user1)
        
        # Test joining - use PUT method as expected by the API
        start_time = time.time()
        response = self.client.put(self.fast_join_url, {'fast_id': self.fast.id})
        join_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        print(f"Join fast endpoint response time: {join_time:.3f}s")
        
        # Test leaving - use PUT method as expected by the API
        start_time = time.time()
        response = self.client.put(self.fast_leave_url, {'fast_id': self.fast.id})
        leave_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        print(f"Leave fast endpoint response time: {leave_time:.3f}s")
    
    @tag('performance')
    def test_fast_participants_endpoint(self):
        """Test the fast participants endpoint."""
        self.client.force_authenticate(user=self.user1)
        
        start_time = time.time()
        response = self.client.get(self.fast_participants_url)
        response_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.json(), list)
        print(f"Fast participants endpoint response time: {response_time:.3f}s")
    
    @tag('performance')
    def test_fast_stats_endpoint(self):
        """Test the fast stats endpoint."""
        # Authenticate the client since stats endpoint requires authentication
        self.client.force_authenticate(user=self.user1)
        
        start_time = time.time()
        response = self.client.get(self.fast_stats_url)
        response_time = time.time() - start_time
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # Check for the actual fields returned by the stats endpoint
        self.assertIn('joined_fasts', data)
        self.assertIn('total_fasts', data)
        print(f"Fast stats endpoint response time: {response_time:.3f}s")
    
    @tag('performance', 'slow')
    def test_endpoints_under_load(self):
        """Test endpoints performance under sequential requests."""
        endpoints = [
            (self.fasts_list_url + f'?church_id={self.church.id}', 'GET'),
            (self.fast_detail_url, 'GET'),
            (self.fast_by_date_url + f'?date={datetime.now().date().strftime("%Y-%m-%d")}&church_id={self.church.id}', 'GET'),
        ]
        
        print("\nEndpoint performance under load:")
        
        for url, method in endpoints:
            times = []
            for _ in range(5):  # Test with 5 sequential requests
                start_time = time.time()
                if method == 'GET':
                    response = self.client.get(url)
                else:
                    response = self.client.post(url)
                end_time = time.time()
                
                # Ensure the request was successful
                self.assertIn(response.status_code, [200, 201, 204])
                times.append(end_time - start_time)
            
            avg_time = sum(times) / len(times)
            print(f"Endpoint {url}: avg response time over {len(times)} requests: {avg_time:.3f}s")
            
            # Performance assertion - should respond within 1 second on average
            self.assertLess(avg_time, 1.0, f"Endpoint {url} took too long: {avg_time:.3f}s") 