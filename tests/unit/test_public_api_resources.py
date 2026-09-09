"""Anonymous contract tests for the public v1 resource routes."""

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import resolve, reverse

from hub.models import Church, Day, Fast, Feast, Reading


class PublicApiResourceTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Public API Church")
        self.other_church = Church.objects.create(name="Other Church")
        self.fast = Fast.objects.create(
            church=self.church,
            name="Great Fast",
            description="A public description.",
            culmination_feast="Pascha",
            culmination_feast_date=date(2026, 4, 5),
        )
        self.fast_day = Day.objects.create(
            church=self.church, fast=self.fast, date=date(2026, 3, 1)
        )

    def test_church_discovery_is_anonymous_and_paginated(self):
        response = self.client.get("/api/v1/churches/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"count", "next", "previous", "results"})
        self.assertGreaterEqual(response.json()["count"], 2)
        self.assertIn(
            {"id": self.church.id, "name": self.church.name}, response.json()["results"]
        )
        self.assertIn(
            {"id": self.other_church.id, "name": self.other_church.name}, response.json()["results"]
        )

    def test_collection_pagination_rejects_invalid_values(self):
        for parameter, value in (
            ("limit", ""),
            ("limit", "abc"),
            ("limit", "0"),
            ("limit", "101"),
            ("offset", "abc"),
            ("offset", "-1"),
        ):
            with self.subTest(parameter=parameter, value=value):
                response = self.client.get(f"/api/v1/churches/?{parameter}={value}")

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_pagination")
                self.assertEqual(
                    response.json()["details"],
                    {"parameter": parameter, "value": value},
                )

    def test_fast_routes_expose_only_the_public_serializer_contract(self):
        list_response = self.client.get(
            f"/api/v1/fasts/?church_id={self.church.id}&start_date=2026-03-01&end_date=2026-03-01"
        )
        detail_response = self.client.get(f"/api/v1/fasts/{self.fast.id}/")
        expected_keys = {
            "id", "church_id", "name", "description", "start_date", "end_date",
            "culmination_feast", "culmination_feast_date", "year", "image_url",
            "thumbnail_url", "learn_more_url",
        }

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(set(list_response.json()["results"][0]), expected_keys)
        self.assertEqual(set(detail_response.json()), expected_keys)
        self.assertEqual(detail_response.json()["start_date"], "2026-03-01")
        self.assertNotIn("countdown", detail_response.json())
        self.assertNotIn("modal_id", detail_response.json())

    def test_fast_date_lookups_are_anonymous_and_require_church_and_date(self):
        by_date = self.client.get(
            f"/api/v1/fasts/by-date/?church_id={self.church.id}&date=2026-03-01"
        )
        by_feast_date = self.client.get(
            f"/api/v1/fasts/by-feast-date/?church_id={self.church.id}&date=2026-04-05"
        )
        missing_date = self.client.get(f"/api/v1/fasts/by-date/?church_id={self.church.id}")

        self.assertEqual(by_date.status_code, 200)
        self.assertEqual(by_date.json()["results"][0]["id"], self.fast.id)
        self.assertEqual(by_feast_date.status_code, 200)
        self.assertEqual(by_feast_date.json()["results"][0]["id"], self.fast.id)
        self.assertEqual(missing_date.status_code, 400)
        self.assertEqual(missing_date.json()["code"], "missing_parameter")
        self.assertEqual(missing_date.json()["details"], {"parameter": "date"})

    def test_fast_detail_returns_the_public_not_found_envelope(self):
        response = self.client.get("/api/v1/fasts/999999/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                "code": "resource_not_found",
                "message": "The requested resource does not exist.",
                "details": {"resource": "fast"},
            },
        )

    def test_readings_lookup_never_creates_a_calendar_day(self):
        target_date = date(2026, 3, 2)
        response = self.client.get(
            f"/api/v1/readings/?church_id={self.church.id}&date={target_date.isoformat()}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"date": "2026-03-02", "readings": []})
        self.assertFalse(Day.objects.filter(church=self.church, date=target_date).exists())

    def test_readings_lookup_returns_citation_only_schema(self):
        reading = Reading.objects.create(
            day=self.fast_day,
            sequence=1,
            book="Matthew",
            start_chapter=5,
            start_verse=1,
            end_chapter=5,
            end_verse=12,
        )
        response = self.client.get(
            f"/api/v1/readings/?church_id={self.church.id}&date=2026-03-01"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["readings"],
            [{
                "id": reading.id, "sequence": 1, "book": "Matthew",
                "start_chapter": 5, "start_verse": 1,
                "end_chapter": 5, "end_verse": 12,
            }],
        )

    @patch("hub.services.feast_service.get_feast_for_date")
    def test_feast_lookup_is_read_only_and_uses_the_public_schema(self, lookup):
        feast = Feast.objects.create(church=self.church, name="Theophany")
        lookup.return_value = {"name_en": "Theophany"}

        response = self.client.get(
            f"/api/v1/feasts/?church_id={self.church.id}&date=2026-01-06"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["date"], "2026-01-06")
        self.assertEqual(response.json()["feast"], {"id": feast.id, "name": "Theophany", "icon": None})
        lookup.assert_called_once_with(date(2026, 1, 6), self.church)

    def test_legacy_fast_days_route_is_not_part_of_public_v1(self):
        response = self.client.get(f"/api/v1/fasts/{self.fast.id}/days/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(resolve("/api/v1/fasts/").namespace, "public_api_v1")
        self.assertEqual(reverse("public_api_v1:church-list"), "/api/v1/churches/")
