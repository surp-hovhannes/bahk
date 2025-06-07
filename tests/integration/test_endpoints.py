"""Tests API endpoints."""
import datetime
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory
from hub.models import Church, Fast, Day, Profile
from hub.views.fast import FastOnDate
from rest_framework import status


class FastOnDateEndpointTests(TestCase):
    """Tests endpoint retrieving fast on a date for a participating user."""
    
    def setUp(self):
        """Set up test data."""
        # Create church
        self.church = Church.objects.create(name="Test Church")
        
        # Create user and profile
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123"
        )
        self.profile = Profile.objects.create(
            user=self.user,
            church=self.church
        )
        
        # Create complete fast
        self.complete_fast = Fast.objects.create(
            name="Complete Fast",
            church=self.church,
            description="complete fast"
        )
        self.complete_fast.profiles.add(self.profile)
        
        # Create days for the fast
        self.sample_day = Day.objects.create(
            date=datetime.date(2024, 3, 25),
            church=self.church
        )
        self.today = Day.objects.create(
            date=datetime.date.today(),
            church=self.church
        )
        self.complete_fast.days.add(self.sample_day, self.today)
        
        # Create API request factory
        self.factory = APIRequestFactory()
    
    def test_fast_on_date_endpoint_variations(self, query_params='', culmination_feast_name=None):
        """Tests endpoint retrieving fast on a date with various parameters."""
        # Create a fast
        fast = Fast.objects.create(
            name="Complete Fast",
            church=self.church,
            culmination_feast=culmination_feast_name,
            culmination_feast_date=datetime.date(2024, 3, 27) if culmination_feast_name else None
        )
        
        # Create a day for the fast
        day = Day.objects.create(
            fast=fast,
            date=datetime.date(2024, 3, 25)
        )
        
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
