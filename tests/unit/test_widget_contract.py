"""Keep the browser fixture grounded in an actual anonymous public API response."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from hub.models import Church, Day, Fast, Reading


@override_settings(
    ROOT_URLCONF="tests.unit.public_api_urls_enabled",
    PUBLIC_API_TRAFFIC_ENABLED=False,
    PUBLIC_API_RESPONSE_CACHE_ENABLED=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
)
class WidgetFixtureContractTests(TestCase):
    def test_browser_fixture_matches_public_calendar(self):
        fixture = json.loads((Path(__file__).parents[1] / "widgets/calendar.json").read_text())
        church, _ = Church.objects.update_or_create(
            id=fixture["church"]["id"], defaults={"name": fixture["church"]["name"]}
        )
        fast = Fast.objects.create(
            id=fixture["fast"]["id"],
            church=church,
            name=fixture["fast"]["name"],
            description=fixture["fast"]["description"],
            url=fixture["fast"]["learn_more_url"],
        )
        day = Day.objects.create(church=church, fast=fast, date=date.fromisoformat(fixture["date"]))
        for reading in fixture["readings"]:
            Reading.objects.create(day=day, **reading)
        with patch("hub.services.feast_service.get_feast_for_date", return_value=[]):
            response = self.client.get(
                "/api/v1/calendar/",
                {"church_id": church.pk, "date": fixture["date"], "lang": "en", "tz": "UTC"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fixture)
