"""Tests API endpoints."""
import datetime

import pytest

from django.urls import reverse
from rest_framework.test import APIRequestFactory

from hub import models, views


@pytest.mark.parametrize("query_params", [
    (""),  # no query params gets fast for today
    ("?date=20240325"),  # matches sample_day_fixture's date
])
@pytest.mark.parametrize("culmination_feast_name", [
    "Culmination Feast",
    None,
])
@pytest.mark.django_db
def test_fast_on_date_endpoint(query_params, culmination_feast_name, complete_fast_fixture, request):
    """Tests endpoint retrieving fast on a date for a participating user."""
    view = views.FastOnDate().as_view()

    user = models.User.objects.filter(profile__fasts=complete_fast_fixture).first()
    church = models.Church.objects.filter(fasts=complete_fast_fixture).first()

    # create expected countdown statement
    countdown = f"1 day until the end of {complete_fast_fixture.name}"
    if culmination_feast_name is not None:
        days_until_feast = 2
        complete_fast_fixture.culmination_feast = culmination_feast_name
        complete_fast_fixture.culmination_feast_date = datetime.date.today() + datetime.timedelta(days=days_until_feast)
        complete_fast_fixture.save(update_fields=["culmination_feast", "culmination_feast_date"])
        countdown = f"{days_until_feast} days until {culmination_feast_name}"

    url = reverse("fast_on_date") + query_params

    factory = APIRequestFactory()
    request = factory.get(url, format="json")
    request.user = user
    response = view(request)

    assert response.status_code == 200
    assert response.data == {
        "name": complete_fast_fixture.name, 
        "church": {
            "name": church.name
        },
        "participant_count": complete_fast_fixture.profiles.count(),
        "description": complete_fast_fixture.description,
        "countdown": countdown
    }
