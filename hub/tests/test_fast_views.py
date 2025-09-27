from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from hub.models import Church, Fast, Day, Profile
from hub.views.fast import FastListView
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIRequestFactory
from rest_framework.test import force_authenticate
from datetime import datetime, timedelta
from django.core.cache import cache
from tests.fixtures.test_data import TestDataFactory
import pytz

User = get_user_model()

class FastListViewTest(TestCase):
    def setUp(self):
        # Create a church using TestDataFactory
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create a user with profile using TestDataFactory
        self.user = TestDataFactory.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church
        )
        
        # Create 3 fasts using TestDataFactory
        self.fasts = []
        today = timezone.now().date()
        
        for i in range(3):
            fast = TestDataFactory.create_fast(
                church=self.church,
                name=f"Test Fast {i}",
                description=f"Test Description {i}"
            )
            self.fasts.append(fast)
            
            # Create days for each fast (30 days before and after today) using TestDataFactory
            for j in range(-30, 31):
                date = today + timedelta(days=j)
                day = TestDataFactory.create_day(
                    date=date,
                    church=self.church
                )
                day.fast = fast
                day.save()
        
        # Associate user with first two fasts
        self.profile.fasts.add(self.fasts[0], self.fasts[1])
        
        # Create API request factory
        self.factory = APIRequestFactory()
        
    def tearDown(self):
        # Clear cache after each test
        cache.clear()
        
    def _create_request(self, user=None, query_params=None):
        """Helper method to create a properly configured request."""
        # Create base request
        request = self.factory.get('/api/fasts/')
        
        # Set up authentication
        if user:
            force_authenticate(request, user=user)
        request.user = user or AnonymousUser()
        
        # Set up query parameters
        request.query_params = query_params or {}
        
        # Set up default timezone if not provided
        if 'tz' not in request.query_params:
            request.query_params['tz'] = 'UTC'
            
        return request
        
    def test_list_fasts_authenticated(self):
        """Test listing fasts for an authenticated user."""
        # Create request
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)  # Should see all fasts
        self.assertEqual(queryset[0].participant_count, 1)  # One participant
        
    def test_list_fasts_unauthenticated(self):
        """Test listing fasts for an unauthenticated user."""
        # Create request with church_id
        request = self._create_request(
            query_params={'church_id': self.church.id}
        )
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)  # Should see all fasts
        
    def test_list_fasts_with_date_range(self):
        """Test listing fasts with specific date range."""
        today = timezone.now().date()
        start_date = today - timedelta(days=10)
        end_date = today + timedelta(days=10)
        
        # Create request with date range
        request = self._create_request(
            user=self.user,
            query_params={
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            }
        )
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)  # All fasts should be in range
        
    def test_list_fasts_with_timezone(self):
        """Test listing fasts with different timezone."""
        # Create request with timezone
        request = self._create_request(
            user=self.user,
            query_params={'tz': 'America/New_York'}
        )
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)
        
    def test_list_fasts_caching(self):
        """Test that fasts are properly cached."""
        # Create request
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # First request - should hit database
        queryset1 = view.get_queryset()
        
        # Second request - should hit cache
        queryset2 = view.get_queryset()
        
        # Verify results are the same
        self.assertEqual(list(queryset1), list(queryset2))
        
    def test_list_fasts_cache_invalidation(self):
        """Test that cache is invalidated when fasts change."""
        # Create request
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # First request - should hit database
        queryset1 = list(view.get_queryset())
        
        # Modify a fast (explicitly set English translation)
        self.fasts[0].set_current_language('en')
        self.fasts[0].name = "Updated Fast"
        self.fasts[0].save()
        
        # Clear cache
        cache.clear()
        
        # Second request - should hit database again
        queryset2 = list(view.get_queryset())
        
        # Verify results are different
        self.assertNotEqual(queryset1[0].name, queryset2[0].name)
        
    def test_list_fasts_with_different_church(self):
        """Test listing fasts for a different church."""
        # Create another church and user
        other_church = TestDataFactory.create_church(name="Other Church")
        other_user = TestDataFactory.create_user(
            username='otheruser@example.com',
            email='otheruser@example.com',
            password='testpass123'
        )
        other_profile = TestDataFactory.create_profile(
            user=other_user,
            church=other_church
        )
        
        # Create a fast for the other church
        other_fast = TestDataFactory.create_fast(
            church=other_church,
            name="Other Church Fast",
            description="Other Description"
        )
        
        # Create days for the other church's fast
        today = timezone.now().date()
        for j in range(-30, 31):
            date = today + timedelta(days=j)
            day = TestDataFactory.create_day(
                date=date,
                church=other_church
            )
            day.fast = other_fast
            day.save()
        
        # Create request
        request = self._create_request(user=other_user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 1)  # Should only see fasts from other church
        self.assertEqual(queryset[0].church, other_church) 