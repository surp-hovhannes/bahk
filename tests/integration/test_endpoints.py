"""Tests API endpoints."""
import datetime
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory

from hub import models, views


class FastOnDateEndpointTests(TestCase):
    """Tests endpoint retrieving fast on a date for a participating user."""
    
    def setUp(self):
        """Set up test data."""
        # Create church
        self.church = models.Church.objects.create(name="Test Church")
        
        # Create user and profile
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123"
        )
        self.profile = models.Profile.objects.create(
            user=self.user,
            church=self.church
        )
        
        # Create complete fast
        self.complete_fast = models.Fast.objects.create(
            name="Complete Fast",
            church=self.church,
            description="complete fast"
        )
        self.complete_fast.profiles.add(self.profile)
        
        # Create days for the fast
        self.sample_day = models.Day.objects.create(
            date=datetime.date(2024, 3, 25),
            church=self.church
        )
        self.today = models.Day.objects.create(
            date=datetime.date.today(),
            church=self.church
        )
        self.complete_fast.days.add(self.sample_day, self.today)
        
        # Create API request factory
        self.factory = APIRequestFactory()
    
    def test_fast_on_date_endpoint_variations(self):
        """Tests endpoint retrieving fast on a date with various parameters."""
        view = views.FastOnDate().as_view()
        
        # Test parameters
        query_params_list = [
            "",  # no query params gets fast for today
            "?date=20240325",  # matches sample_day's date
        ]
        culmination_feast_names = [
            "Culmination Feast",
            None,
        ]
        
        for query_params in query_params_list:
            for culmination_feast_name in culmination_feast_names:
                with self.subTest(
                    query_params=query_params, 
                    culmination_feast_name=culmination_feast_name
                ):
                    # Reset fast to original state
                    self.complete_fast.culmination_feast = None
                    self.complete_fast.culmination_feast_date = None
                    self.complete_fast.save()
                    
                    # Create expected countdown statement
                    countdown = f"1 day until the end of {self.complete_fast.name}"
                    
                    if culmination_feast_name is not None:
                        days_until_feast = 2
                        self.complete_fast.culmination_feast = culmination_feast_name
                        self.complete_fast.culmination_feast_date = (
                            datetime.date.today() + datetime.timedelta(days=days_until_feast)
                        )
                        self.complete_fast.save(
                            update_fields=["culmination_feast", "culmination_feast_date"]
                        )
                        countdown = f"{days_until_feast} days until {culmination_feast_name}"
                    
                    # Create request
                    url = reverse("fast_on_date") + query_params
                    request = self.factory.get(url, format="json")
                    request.user = self.user
                    
                    # Get response
                    response = view(request)
                    
                    # Assertions
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data["name"], self.complete_fast.name)
                    self.assertEqual(response.data["church"]["name"], self.church.name)
                    self.assertEqual(
                        response.data["participant_count"], 
                        self.complete_fast.profiles.count()
                    )
                    self.assertEqual(response.data["description"], self.complete_fast.description)
                    self.assertEqual(response.data["countdown"], countdown)
