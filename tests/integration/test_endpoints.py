"""Tests API endpoints."""
import datetime

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from hub import models, views

@pytest.fixture
def date_query(sample_day_fixture):
    """Fixture to provide a date query string."""
    return "?date=" + str(sample_day_fixture.date.strftime("%Y%m%d"))


@pytest.mark.django_db
@pytest.mark.parametrize("query_params", [
    "",  # no query params gets fast for today
    pytest.param("date_query", marks=pytest.mark.usefixtures("date_query"))
])
def test_complete_fast_on_date_endpoint(query_params, complete_fast_fixture, sample_user_fixture, sample_church_fixture, date_query):
    """Tests endpoint retrieving fast on a date for a participating user."""
    
    client = APIClient()
    user = sample_user_fixture
    client.force_authenticate(user=user)
    
    complete_fast_fixture.profiles.add(user.profile)
    
    days_until_feast = (complete_fast_fixture.culmination_feast_date - datetime.date.today()).days

    countdown = f"<span class='days_to_finish'>{days_until_feast}</span> days until {complete_fast_fixture.culmination_feast}"
 
    url = reverse("fast_on_date") + (date_query if query_params == "date_query" else "")

    response = client.get(url, format="json")

    assert response.status_code == 200

    response_data = {
        "name": response.data["name"], 
        "church": response.data["church"],
        "participant_count": response.data["participant_count"],
        "description": response.data["description"],
        "countdown": response.data["countdown"]
    }
    
    expected_data = {
        "name": complete_fast_fixture.name, 
        "church": {
            "name": sample_church_fixture.name
        },
        "participant_count": complete_fast_fixture.profiles.count(),
        "description": complete_fast_fixture.description,
        "countdown": countdown
    }
    
    assert response_data == expected_data


@pytest.mark.django_db
@pytest.mark.parametrize("query_params", [
    "",  # no query params gets fast for today
    pytest.param("date_query", marks=pytest.mark.usefixtures("date_query"))
])
def test_incomplete_fast_on_date_endpoint(query_params, incomplete_fast_fixture, sample_user_fixture, sample_church_fixture, date_query):
    """Tests endpoint retrieving fast on a date for a participating user."""
    
    client = APIClient()
    user = sample_user_fixture
    client.force_authenticate(user=user)
    
    url = reverse("fast_on_date") + (date_query if query_params == "date_query" else "")

    response = client.get(url, format="json")

    assert response.status_code == 200

    days_until_fast_ends = (incomplete_fast_fixture.days.first().date - datetime.date.today()).days +1

    countdown = f"<span class='days_to_finish'>{days_until_fast_ends}</span> days until the end of {incomplete_fast_fixture.name}"

    response_data = {
        "name": response.data["name"], 
        "church": response.data["church"],
        "participant_count": response.data["participant_count"],
        "description": response.data["description"],
        "countdown": response.data["countdown"]
    }
    
    expected_data = {
        "name": incomplete_fast_fixture.name, 
        "church": {
            "name": sample_church_fixture.name
        },
        "participant_count": incomplete_fast_fixture.profiles.count(),
        "description": incomplete_fast_fixture.description,
        "countdown": countdown
    }
    
    assert response_data == expected_data
