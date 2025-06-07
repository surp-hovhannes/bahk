"""
Tests for memory leaks and performance issues in profile-related endpoints.

This module tests profile endpoints under load conditions with users that have
many fasts associated with them to identify N+1 query problems and memory leaks.
"""

import time
import tracemalloc
from datetime import date, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.db import connection
from django.test.utils import override_settings
from hub.models import Church, Fast, Day, Profile
from tests.fixtures.test_data import TestDataFactory


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class ProfileMemoryLeakTestCase(APITestCase):
    """Test case for detecting memory leaks in profile-related endpoints."""
    
    def setUp(self):
        """Set up test data with a user having many fasts."""
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create test user
        self.user = TestDataFactory.create_user(
            username="testuser@example.com",
            email="testuser@example.com",
            password="testpass123"
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            name="Test User"
        )
        
        # Authenticate client
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        # URLs to test
        self.profile_url = reverse('profile-detail')
        self.fast_stats_url = reverse('fast-stats')
        
    def create_fasts_with_days(self, num_fasts=10, days_per_fast=10):
        """Create multiple fasts with many days for testing N+1 queries."""
        fasts = []
        
        for i in range(num_fasts):
            # Create fast
            fast = TestDataFactory.create_fast(
                name=f"Test Fast {i}",
                church=self.church,
                description=f"Description for fast {i}"
            )
            
            # Create days for the fast
            base_date = date.today() - timedelta(days=days_per_fast)
            for j in range(days_per_fast):
                day = TestDataFactory.create_day(
                    date=base_date + timedelta(days=j),
                    church=self.church
                )
                fast.days.add(day)
            
            # Add user to the fast
            self.profile.fasts.add(fast)
            fasts.append(fast)
            
        return fasts
    
    def count_queries(self, func):
        """Count the number of database queries executed by a function."""
        # Reset queries log
        connection.queries_log.clear()
        
        # Enable query logging
        from django.conf import settings
        old_debug = settings.DEBUG
        settings.DEBUG = True
        
        try:
            func()
            query_count = len(connection.queries)
        finally:
            settings.DEBUG = old_debug
            
        return query_count
    
    def measure_memory_usage(self, func):
        """Measure memory usage of a function."""
        tracemalloc.start()
        func()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return current, peak
    
    def test_fast_stats_n_plus_one_query_problem(self):
        """Test that FastStatsView has N+1 query problems with many fasts."""
        # Test with small number of fasts first
        self.create_fasts_with_days(num_fasts=5, days_per_fast=10)
        
        def get_fast_stats():
            return self.client.get(self.fast_stats_url)
        
        # Count queries with 5 fasts
        queries_5_fasts = self.count_queries(get_fast_stats)
        response = self.client.get(self.fast_stats_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Add more fasts and test again
        self.create_fasts_with_days(num_fasts=10, days_per_fast=10)
        
        # Count queries with 15 fasts total
        queries_15_fasts = self.count_queries(get_fast_stats)
        
        # The difference should be concerning - each additional fast should add a query
        query_growth = queries_15_fasts - queries_5_fasts
        
        print(f"Queries with 5 fasts: {queries_5_fasts}")
        print(f"Queries with 15 fasts: {queries_15_fasts}")
        print(f"Query growth: {query_growth}")
        
        # This test will likely fail due to N+1 queries
        # Each additional fast adds at least one query for fast.days.count()
        self.assertLess(query_growth, 15, 
                       "FastStatsView has N+1 query problem - queries grow linearly with fasts")
    
    def test_fast_stats_memory_usage_with_many_fasts(self):
        """Test memory usage of FastStatsView with many fasts."""
        # Create user with many fasts
        self.create_fasts_with_days(num_fasts=50, days_per_fast=20)
        
        def get_fast_stats():
            response = self.client.get(self.fast_stats_url)
            return response.data
        
        # Measure memory usage
        current, peak = self.measure_memory_usage(get_fast_stats)
        
        print(f"Current memory usage: {current / 1024 / 1024:.2f} MB")
        print(f"Peak memory usage: {peak / 1024 / 1024:.2f} MB")
        
        # Get the response to check data
        response = self.client.get(self.fast_stats_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.data
        self.assertEqual(data['total_fasts'], 50)
        self.assertEqual(data['total_fast_days'], 50 * 20)  # 1000 days total
        
        # Memory usage should be reasonable (less than 10MB for this test)
        self.assertLess(peak / 1024 / 1024, 10, 
                       "Memory usage is too high for FastStatsView")
    
    def test_fast_stats_response_time_with_many_fasts(self):
        """Test response time of FastStatsView with many fasts."""
        # Create user with many fasts
        self.create_fasts_with_days(num_fasts=30, days_per_fast=15)
        
        # Measure response time
        start_time = time.time()
        response = self.client.get(self.fast_stats_url)
        response_time = time.time() - start_time
        
        print(f"FastStatsView response time with 30 fasts: {response_time:.3f}s")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Response should be under 1 second
        self.assertLess(response_time, 1.0, 
                       "FastStatsView response time is too slow")
    
    def test_profile_detail_view_performance(self):
        """Test ProfileDetailView performance with user having many fasts."""
        # Create user with many fasts
        self.create_fasts_with_days(num_fasts=25, days_per_fast=12)
        
        def get_profile():
            return self.client.get(self.profile_url)
        
        # Count queries
        query_count = self.count_queries(get_profile)
        
        # Measure response time
        start_time = time.time()
        response = self.client.get(self.profile_url)
        response_time = time.time() - start_time
        
        print(f"ProfileDetailView queries: {query_count}")
        print(f"ProfileDetailView response time: {response_time:.3f}s")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Profile endpoint should be efficient - not dependent on number of fasts
        self.assertLess(query_count, 10, 
                       "ProfileDetailView should not have many queries")
        self.assertLess(response_time, 0.5, 
                       "ProfileDetailView response time should be fast")
    
    def test_fast_participants_view_with_many_participants(self):
        """Test FastParticipantsView with a fast having many participants."""
        # Create a fast
        fast = TestDataFactory.create_fast(
            name="Popular Fast",
            church=self.church
        )
        
        # Add days to the fast
        base_date = date.today()
        for i in range(10):
            day = TestDataFactory.create_day(
                date=base_date + timedelta(days=i),
                church=self.church
            )
            fast.days.add(day)
        
        # Create many participants
        participants = []
        for i in range(100):
            user = TestDataFactory.create_user(
                username=f"participant{i}@example.com",
                email=f"participant{i}@example.com",
                password="testpass123"
            )
            profile = TestDataFactory.create_profile(
                user=user,
                church=self.church,
                name=f"Participant {i}"
            )
            profile.fasts.add(fast)
            participants.append(profile)
        
        # Test participants endpoint
        participants_url = reverse('fast-participants', kwargs={'fast_id': fast.id})
        
        def get_participants():
            return self.client.get(participants_url)
        
        # Count queries
        query_count = self.count_queries(get_participants)
        
        # Measure response time
        start_time = time.time()
        response = self.client.get(participants_url)
        response_time = time.time() - start_time
        
        print(f"FastParticipantsView queries with 100 participants: {query_count}")
        print(f"FastParticipantsView response time: {response_time:.3f}s")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Should be efficient regardless of participant count
        self.assertLess(query_count, 15, 
                       "FastParticipantsView should have efficient queries")
        self.assertLess(response_time, 1.0, 
                       "FastParticipantsView should respond quickly")
    
    def test_paginated_participants_view_performance(self):
        """Test PaginatedFastParticipantsView performance."""
        # Create a fast with many participants (reuse from previous test setup)
        fast = TestDataFactory.create_fast(
            name="Another Popular Fast",
            church=self.church
        )
        
        # Create many participants
        for i in range(200):
            user = TestDataFactory.create_user(
                username=f"paginated_participant{i}@example.com",
                email=f"paginated_participant{i}@example.com",
                password="testpass123"
            )
            profile = TestDataFactory.create_profile(
                user=user,
                church=self.church,
                name=f"Paginated Participant {i}"
            )
            profile.fasts.add(fast)
        
        # Test paginated participants endpoint
        paginated_url = reverse('fast-participants-paginated', kwargs={'fast_id': fast.id})
        
        def get_paginated_participants():
            return self.client.get(paginated_url + '?limit=20&offset=0')
        
        # Count queries
        query_count = self.count_queries(get_paginated_participants)
        
        # Measure response time
        start_time = time.time()
        response = self.client.get(paginated_url + '?limit=20&offset=0')
        response_time = time.time() - start_time
        
        print(f"PaginatedFastParticipantsView queries: {query_count}")
        print(f"PaginatedFastParticipantsView response time: {response_time:.3f}s")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Pagination should keep queries constant
        self.assertLess(query_count, 10, 
                       "PaginatedFastParticipantsView should have constant queries")
        self.assertLess(response_time, 0.5, 
                       "PaginatedFastParticipantsView should be fast")


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class ProfileStressTestCase(APITestCase):
    """Stress tests for profile endpoints under extreme load."""
    
    def setUp(self):
        """Set up test data for stress testing."""
        self.church = TestDataFactory.create_church(name="Stress Test Church")
        
        self.user = TestDataFactory.create_user(
            username="stressuser@example.com",
            email="stressuser@example.com",
            password="testpass123"
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            name="Stress Test User"
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.fast_stats_url = reverse('fast-stats')
    
    def test_extreme_fast_stats_load(self):
        """Test FastStatsView with extreme number of fasts."""
        # Create user with extreme number of fasts
        num_fasts = 100  # Reduced for testing
        days_per_fast = 20
        
        print(f"Creating {num_fasts} fasts with {days_per_fast} days each...")
        
        for i in range(num_fasts):
            fast = TestDataFactory.create_fast(
                name=f"Extreme Fast {i}",
                church=self.church
            )
            
            # Create days for each fast
            base_date = date.today() - timedelta(days=days_per_fast)
            for j in range(days_per_fast):
                day = TestDataFactory.create_day(
                    date=base_date + timedelta(days=j),
                    church=self.church
                )
                fast.days.add(day)
            
            self.profile.fasts.add(fast)
            
            # Print progress every 25 fasts
            if (i + 1) % 25 == 0:
                print(f"Created {i + 1} fasts...")
        
        print("Testing FastStatsView under extreme load...")
        
        # Test the endpoint
        start_time = time.time()
        
        # Start memory tracing
        tracemalloc.start()
        
        response = self.client.get(self.fast_stats_url)
        
        # Get memory usage
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        response_time = time.time() - start_time
        
        print(f"Extreme load test results:")
        print(f"  - Fasts: {num_fasts}")
        print(f"  - Total days: {num_fasts * days_per_fast}")
        print(f"  - Response time: {response_time:.3f}s")
        print(f"  - Peak memory: {peak / 1024 / 1024:.2f} MB")
        print(f"  - Current memory: {current / 1024 / 1024:.2f} MB")
        
        # Assertions for acceptable performance
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Even with extreme load, response should be under 5 seconds
        self.assertLess(response_time, 5.0, 
                       f"Response time {response_time:.3f}s is too slow for extreme load")
        
        # Memory usage should be reasonable (under 50MB)
        self.assertLess(peak / 1024 / 1024, 50, 
                       f"Memory usage {peak / 1024 / 1024:.2f}MB is too high")
        
        # Verify response data is correct
        data = response.data
        self.assertEqual(data['total_fasts'], num_fasts)
        self.assertEqual(data['total_fast_days'], num_fasts * days_per_fast)