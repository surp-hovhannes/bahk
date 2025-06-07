"""
Tests to verify the performance improvements in optimized profile endpoints.

This module tests the optimized profile endpoints to ensure they perform better
than the original implementations and don't have memory leaks or N+1 query problems.

Test Tags:
- @tag('performance'): Tests that check performance metrics but run relatively quickly
- @tag('performance', 'slow'): Tests that create large datasets and take longer to run

Usage:
- Run all tests: python manage.py test tests.test_optimized_profile_endpoints
- Run only fast performance tests: python manage.py test tests.test_optimized_profile_endpoints --tag=performance --exclude-tag=slow
- Skip all slow tests: python manage.py test tests.test_optimized_profile_endpoints --exclude-tag=slow
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
from django.test.utils import override_settings, tag
from hub.models import Church, Fast, Day, Profile
from tests.fixtures.test_data import TestDataFactory


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class OptimizedProfileEndpointTests(APITestCase):
    """Test case to verify performance improvements in optimized profile endpoints."""
    
    def setUp(self):
        """Set up test data with a user having many fasts."""
        self.church = TestDataFactory.create_church(name="Optimized Test Church")
        
        # Create test user
        self.user = TestDataFactory.create_user(
            username="optimizeduser@example.com",
            email="optimizeduser@example.com",
            password="testpass123"
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            name="Optimized Test User"
        )
        
        # Authenticate client
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        # URLs to test
        self.profile_url = reverse('profile-detail')
        self.fast_stats_url = reverse('fast-stats')
        
    def create_fasts_with_days(self, num_fasts=10, days_per_fast=10):
        """Create multiple fasts with many days for testing."""
        fasts = []
        
        for i in range(num_fasts):
            # Create fast
            fast = TestDataFactory.create_fast(
                name=f"Optimized Fast {i}",
                church=self.church,
                description=f"Description for optimized fast {i}"
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
    
    @tag('performance')
    def test_optimized_fast_stats_query_efficiency(self):
        """Test that optimized FastStatsView has constant query count regardless of number of fasts."""
        # Test with few fasts
        self.create_fasts_with_days(num_fasts=5, days_per_fast=10)
        
        def get_fast_stats():
            return self.client.get(self.fast_stats_url)
        
        # Count queries with 5 fasts
        queries_5_fasts = self.count_queries(get_fast_stats)
        response = self.client.get(self.fast_stats_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Add more fasts
        self.create_fasts_with_days(num_fasts=20, days_per_fast=15)
        
        # Count queries with 25 fasts total
        queries_25_fasts = self.count_queries(get_fast_stats)
        response_25_fasts = self.client.get(self.fast_stats_url)
        
        # The query count should remain relatively constant (not grow linearly)
        query_growth = queries_25_fasts - queries_5_fasts
        
        print(f"Optimized - Queries with 5 fasts: {queries_5_fasts}")
        print(f"Optimized - Queries with 25 fasts: {queries_25_fasts}")
        print(f"Optimized - Query growth: {query_growth}")
        
        # With our optimization, query growth should be minimal (< 5 additional queries)
        self.assertLess(query_growth, 5, 
                       "Optimized FastStatsView should have minimal query growth")
        
        # Verify response correctness
        self.assertEqual(response_25_fasts.status_code, status.HTTP_200_OK)
        data = response_25_fasts.data
        self.assertEqual(data['total_fasts'], 25)
        # First batch: 5 fasts * 10 days = 50 days
        # Second batch: 20 fasts * 15 days = 300 days
        # Total: 350 days
        self.assertEqual(data['total_fast_days'], 5 * 10 + 20 * 15)
    
    @tag('performance', 'slow')
    def test_optimized_fast_stats_performance(self):
        """Test that optimized FastStatsView has excellent performance with many fasts."""
        # Create many fasts
        self.create_fasts_with_days(num_fasts=100, days_per_fast=25)
        
        # Measure performance
        start_time = time.time()
        
        # Start memory tracking
        tracemalloc.start()
        
        response = self.client.get(self.fast_stats_url)
        
        # Get memory usage
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        response_time = time.time() - start_time
        
        print(f"Optimized FastStatsView with 100 fasts:")
        print(f"  - Response time: {response_time:.3f}s")
        print(f"  - Peak memory: {peak / 1024 / 1024:.2f} MB")
        print(f"  - Current memory: {current / 1024 / 1024:.2f} MB")
        
        # Performance should be excellent
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLess(response_time, 0.5, 
                       "Optimized FastStatsView should respond in under 0.5s")
        self.assertLess(peak / 1024 / 1024, 5, 
                       "Optimized memory usage should be under 5MB")
        
        # Verify correctness
        data = response.data
        self.assertEqual(data['total_fasts'], 100)
        self.assertEqual(data['total_fast_days'], 100 * 25)
    
    @tag('performance')
    def test_optimized_profile_detail_performance(self):
        """Test that optimized ProfileDetailView has good performance."""
        # Create user with many fasts
        self.create_fasts_with_days(num_fasts=50, days_per_fast=20)
        
        def get_profile():
            return self.client.get(self.profile_url)
        
        # Count queries
        query_count = self.count_queries(get_profile)
        
        # Measure response time
        start_time = time.time()
        response = self.client.get(self.profile_url)
        response_time = time.time() - start_time
        
        print(f"Optimized ProfileDetailView:")
        print(f"  - Queries: {query_count}")
        print(f"  - Response time: {response_time:.3f}s")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Optimized version should have very few queries
        self.assertLess(query_count, 5, 
                       "Optimized ProfileDetailView should have minimal queries")
        self.assertLess(response_time, 0.2, 
                       "Optimized ProfileDetailView should be very fast")
    
    @tag('performance', 'slow')
    def test_optimized_vs_baseline_comparison(self):
        """Compare optimized performance against baseline metrics."""
        # Create substantial test data
        num_fasts = 75
        days_per_fast = 30
        self.create_fasts_with_days(num_fasts=num_fasts, days_per_fast=days_per_fast)
        
        # Test FastStatsView performance
        start_time = time.time()
        response = self.client.get(self.fast_stats_url)
        fast_stats_time = time.time() - start_time
        
        # Test ProfileDetailView performance
        start_time = time.time()
        profile_response = self.client.get(self.profile_url)
        profile_time = time.time() - start_time
        
        print(f"\nOptimized Performance Summary:")
        print(f"  - Test data: {num_fasts} fasts with {days_per_fast} days each")
        print(f"  - Total days: {num_fasts * days_per_fast}")
        print(f"  - FastStatsView time: {fast_stats_time:.3f}s")
        print(f"  - ProfileDetailView time: {profile_time:.3f}s")
        
        # Both endpoints should respond quickly
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_response.status_code, status.HTTP_200_OK)
        
        # Performance targets for optimized endpoints
        self.assertLess(fast_stats_time, 0.3, 
                       "Optimized FastStatsView should be under 0.3s")
        self.assertLess(profile_time, 0.1, 
                       "Optimized ProfileDetailView should be under 0.1s")
        
        # Verify data correctness
        stats_data = response.data
        self.assertEqual(stats_data['total_fasts'], num_fasts)
        self.assertEqual(stats_data['total_fast_days'], num_fasts * days_per_fast)
    
    @tag('performance', 'slow')
    def test_memory_usage_stability(self):
        """Test that memory usage remains stable with large datasets."""
        # Create increasingly large datasets and verify memory doesn't grow excessively
        memory_measurements = []
        total_fasts = 0
        
        for batch_size in [25, 50, 75]:
            # Add more fasts
            self.create_fasts_with_days(num_fasts=batch_size, days_per_fast=20)
            total_fasts += batch_size
            
            # Measure memory for this batch
            tracemalloc.start()
            response = self.client.get(self.fast_stats_url)
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            
            memory_measurements.append({
                'fasts': total_fasts,  # Track cumulative total
                'peak_mb': peak / 1024 / 1024,
                'current_mb': current / 1024 / 1024
            })
            
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        print(f"\nMemory Usage Stability Test:")
        for measurement in memory_measurements:
            print(f"  - {measurement['fasts']} fasts: Peak {measurement['peak_mb']:.2f}MB, Current {measurement['current_mb']:.2f}MB")
        
        # Memory usage should not grow excessively
        for measurement in memory_measurements:
            self.assertLess(measurement['peak_mb'], 10, 
                           f"Memory usage with {measurement['fasts']} fasts should be under 10MB")


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class OptimizedEndpointStressTest(APITestCase):
    """Stress test for optimized endpoints under extreme load."""
    
    def setUp(self):
        """Set up test data for stress testing."""
        self.church = TestDataFactory.create_church(name="Stress Test Church Optimized")
        
        self.user = TestDataFactory.create_user(
            username="stressuseroptimized@example.com",
            email="stressuseroptimized@example.com",
            password="testpass123"
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            name="Stress Test User Optimized"
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.fast_stats_url = reverse('fast-stats')
    
    @tag('performance', 'slow')
    def test_extreme_load_optimized_fast_stats(self):
        """Test optimized FastStatsView under extreme load."""
        # Create extreme dataset
        num_fasts = 200
        days_per_fast = 40
        
        print(f"Creating {num_fasts} fasts with {days_per_fast} days each for optimized stress test...")
        
        for i in range(num_fasts):
            fast = TestDataFactory.create_fast(
                name=f"Extreme Optimized Fast {i}",
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
            
            # Print progress
            if (i + 1) % 50 == 0:
                print(f"Created {i + 1} fasts...")
        
        print("Testing optimized FastStatsView under extreme load...")
        
        # Test the endpoint
        start_time = time.time()
        tracemalloc.start()
        
        response = self.client.get(self.fast_stats_url)
        
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        response_time = time.time() - start_time
        
        print(f"Optimized extreme load test results:")
        print(f"  - Fasts: {num_fasts}")
        print(f"  - Total days: {num_fasts * days_per_fast}")
        print(f"  - Response time: {response_time:.3f}s")
        print(f"  - Peak memory: {peak / 1024 / 1024:.2f} MB")
        print(f"  - Current memory: {current / 1024 / 1024:.2f} MB")
        
        # Even under extreme load, optimized version should perform well
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Optimized version should handle extreme load much better
        self.assertLess(response_time, 2.0, 
                       f"Optimized response time {response_time:.3f}s should be under 2s")
        self.assertLess(peak / 1024 / 1024, 20, 
                       f"Optimized memory usage {peak / 1024 / 1024:.2f}MB should be under 20MB")
        
        # Verify correctness
        data = response.data
        self.assertEqual(data['total_fasts'], num_fasts)
        self.assertEqual(data['total_fast_days'], num_fasts * days_per_fast)