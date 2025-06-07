"""Tests API endpoints."""
import datetime
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory
from hub.models import Church, Fast, Day, Profile
from hub.views.fast import FastOnDate
from rest_framework import status
from tests.fixtures.test_data import TestDataFactory


class FastOnDateEndpointTests(TestCase):
    """Tests endpoint retrieving fast on a date for a participating user."""
    
    def setUp(self):
        """Set up test data."""
        # Use TestDataFactory for consistent setup
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create user and profile using TestDataFactory
        self.user = TestDataFactory.create_user(
            username="testuser@example.com",
            email="test@example.com",
            password="testpass123"
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church
        )
        
        # Create complete fast using TestDataFactory
        self.complete_fast = TestDataFactory.create_fast(
            name="Complete Fast",
            church=self.church,
            description="complete fast"
        )
        self.complete_fast.profiles.add(self.profile)
        
        # Create days for the fast using TestDataFactory
        self.sample_day = TestDataFactory.create_day(
            date=datetime.date(2024, 3, 25),
            church=self.church
        )
        self.today = TestDataFactory.create_day(
            date=datetime.date.today(),
            church=self.church
        )
        self.complete_fast.days.add(self.sample_day, self.today)
        
        # Create API request factory
        self.factory = APIRequestFactory()
    
    def test_fast_on_date_endpoint_variations(self, query_params='', culmination_feast_name=None):
        """Tests endpoint retrieving fast on a date with various parameters."""
        # Create a fast using TestDataFactory
        fast = TestDataFactory.create_fast(
            name="Complete Fast",
            church=self.church,
            description="A test fast"
        )
        
        # Set additional attributes that TestDataFactory doesn't handle
        if culmination_feast_name:
            fast.culmination_feast = culmination_feast_name
            fast.culmination_feast_date = datetime.date(2024, 3, 27)
            fast.save()
        
        # Create a day for the fast using TestDataFactory
        day = TestDataFactory.create_day(
            date=datetime.date(2024, 3, 25),
            church=self.church
        )
        day.fast = fast
        day.save()
        
        # Calculate expected countdown
        days_remaining = 2 if culmination_feast_name else 1
        day_word = "day" if days_remaining == 1 else "days"
        countdown = f"<span class='days_to_finish'>{days_remaining}</span> {day_word} until {culmination_feast_name if culmination_feast_name else 'the end of Complete Fast'}"
        
        # For anonymous users, we need to provide church_id
        if query_params:
            url = reverse('fast_on_date') + query_params + f"&church_id={self.church.id}"
        else:
            url = reverse('fast_on_date') + f"?church_id={self.church.id}"
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["countdown"], countdown)
