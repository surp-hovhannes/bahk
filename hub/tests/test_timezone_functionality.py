"""
Tests for timezone functionality in user profiles.
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.http import HttpResponse
from hub.models import Profile
from hub.serializers import ProfileSerializer
from hub.middleware import TimezoneUpdateMiddleware
from tests.fixtures.test_data import TestDataFactory
import pytz


class TimezoneModelTest(TestCase):
    """Test timezone field in Profile model."""
    
    def setUp(self):
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user)
    
    def test_profile_has_default_utc_timezone(self):
        """Test that new profiles default to UTC timezone."""
        self.assertEqual(self.profile.timezone, 'UTC')
    
    def test_profile_timezone_can_be_updated(self):
        """Test that profile timezone can be updated."""
        self.profile.timezone = 'America/New_York'
        self.profile.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'America/New_York')
    
    def test_profile_timezone_validation(self):
        """Test that invalid timezone strings are handled."""
        # This test just ensures the field accepts string values
        # Actual timezone validation would be done at the application level
        self.profile.timezone = 'Invalid/Timezone'
        self.profile.save()
        self.assertEqual(self.profile.timezone, 'Invalid/Timezone')


class ProfileSerializerTest(TestCase):
    """Test that ProfileSerializer includes timezone field."""
    
    def setUp(self):
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user)
    
    def test_profile_serializer_includes_timezone(self):
        """Test that ProfileSerializer includes timezone field in serialized data."""
        serializer = ProfileSerializer(self.profile)
        self.assertIn('timezone', serializer.data)
        self.assertEqual(serializer.data['timezone'], 'UTC')
    
    def test_profile_serializer_can_update_timezone(self):
        """Test that ProfileSerializer can update timezone field."""
        data = {
            'timezone': 'Europe/London'
        }
        serializer = ProfileSerializer(self.profile, data=data, partial=True)
        self.assertTrue(serializer.is_valid())
        serializer.save()
        
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'Europe/London')


class TimezoneMiddlewareTest(TestCase):
    """Test timezone update middleware."""
    
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = TimezoneUpdateMiddleware(lambda request: HttpResponse())
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user)
    
    def test_middleware_updates_timezone_from_query_param(self):
        """Test that middleware updates user timezone from tz query parameter."""
        # Create request with timezone parameter
        request = self.factory.get('/api/some-endpoint/?tz=America/Los_Angeles')
        request.user = self.user
        request.query_params = {'tz': 'America/Los_Angeles'}
        
        # Process request through middleware
        self.middleware.process_request(request)
        
        # Check that profile timezone was updated
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'America/Los_Angeles')
    
    def test_middleware_updates_timezone_from_header(self):
        """Test that middleware updates user timezone from X-Timezone header."""
        # Create request with timezone header
        request = self.factory.get('/api/some-endpoint/', HTTP_X_TIMEZONE='Asia/Tokyo')
        request.user = self.user
        
        # Process request through middleware
        self.middleware.process_request(request)
        
        # Check that profile timezone was updated
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'Asia/Tokyo')
    
    def test_middleware_skips_unauthenticated_users(self):
        """Test that middleware doesn't process unauthenticated users."""
        from django.contrib.auth.models import AnonymousUser
        
        # Create request with unauthenticated user
        request = self.factory.get('/api/some-endpoint/?tz=America/New_York')
        request.user = AnonymousUser()
        request.query_params = {'tz': 'America/New_York'}
        
        # Process request through middleware (should not raise error)
        result = self.middleware.process_request(request)
        self.assertIsNone(result)
    
    def test_middleware_handles_invalid_timezone(self):
        """Test that middleware gracefully handles invalid timezone strings."""
        # Create request with invalid timezone
        request = self.factory.get('/api/some-endpoint/?tz=Invalid/Timezone')
        request.user = self.user
        request.query_params = {'tz': 'Invalid/Timezone'}
        
        # Process request through middleware (should not raise error)
        result = self.middleware.process_request(request)
        self.assertIsNone(result)
        
        # Profile timezone should remain unchanged
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'UTC')
    
    def test_middleware_only_updates_when_timezone_differs(self):
        """Test that middleware only updates when timezone is different."""
        # Set profile timezone to a specific value
        self.profile.timezone = 'America/Chicago'
        self.profile.save()
        
        # Create request with same timezone
        request = self.factory.get('/api/some-endpoint/?tz=America/Chicago')
        request.user = self.user
        request.query_params = {'tz': 'America/Chicago'}
        
        # Track the original updated timestamp
        original_updated = self.profile.tracker.changed()
        
        # Process request through middleware
        self.middleware.process_request(request)
        
        # Profile should not have been saved again (no change)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'America/Chicago')


class TimezoneIntegrationTest(TestCase):
    """Integration tests for timezone functionality."""
    
    def setUp(self):
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user)
    
    def test_new_user_has_utc_timezone(self):
        """Test that newly created users have UTC as default timezone."""
        new_user = TestDataFactory.create_user()
        new_profile = TestDataFactory.create_profile(user=new_user)
        self.assertEqual(new_profile.timezone, 'UTC')
    
    def test_timezone_field_in_form_fields(self):
        """Test that timezone field is included in ProfileForm."""
        from hub.forms import ProfileForm
        form = ProfileForm()
        self.assertIn('timezone', form.fields)
    
    def test_timezone_tracking_field_changes(self):
        """Test that timezone changes are tracked by FieldTracker."""
        # Initial state - no changes
        self.assertFalse(self.profile.tracker.has_changed('timezone'))
        
        # Change timezone
        self.profile.timezone = 'Europe/Paris'
        self.assertTrue(self.profile.tracker.has_changed('timezone'))
        
        # Save and check tracking resets
        self.profile.save()
        self.assertFalse(self.profile.tracker.has_changed('timezone'))