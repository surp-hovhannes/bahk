"""Tests API endpoints."""
import pytest

from django.urls import reverse
from rest_framework.test import APIRequestFactory

from hub import models, views


@pytest.mark.parametrize("query_params", [
    (""),  # no query params gets fast for today
    ("?date=20240325"),  # matches sample_day_fixture's date
])
@pytest.mark.django_db
def test_fast_on_date_endpoint(query_params, complete_fast_fixture, request):
    """Tests endpoint retrieving fast on a date for a participating user."""
    view = views.FastOnDate().as_view()

    user = models.User.objects.filter(profile__fasts=complete_fast_fixture).first()
    church = models.Church.objects.filter(fasts=complete_fast_fixture).first()

    url = reverse("fast_on_date") + query_params

    factory = APIRequestFactory()
    request = factory.get(url, format="json")
    request.user = user
    response = view(request)

    assert response.status_code == 200
    assert response.data["name"] == complete_fast_fixture.name
    assert response.data["church"]["name"] == church.name
    assert response.data["participant_count"] == complete_fast_fixture.profiles.count()