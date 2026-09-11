"""Anonymous contract tests for the public v1 resource routes."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from hub.models import Church, Day, Fast, Feast, Reading
from icons.models import Icon


@override_settings(
    ROOT_URLCONF="tests.unit.public_api_urls_enabled",
    PUBLIC_API_RESOURCES_ENABLED=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
)
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
        self.fast_day = Day.objects.create(church=self.church, fast=self.fast, date=date(2026, 3, 1))

    def test_church_discovery_is_anonymous_and_paginated(self):
        response = self.client.get("/api/v1/churches/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"count", "next", "previous", "results"})
        self.assertGreaterEqual(response.json()["count"], 2)
        self.assertIn({"id": self.church.id, "name": self.church.name}, response.json()["results"])
        self.assertIn({"id": self.other_church.id, "name": self.other_church.name}, response.json()["results"])

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
            "id",
            "church_id",
            "name",
            "description",
            "start_date",
            "end_date",
            "culmination_feast",
            "culmination_feast_date",
            "year",
            "image_url",
            "thumbnail_url",
            "learn_more_url",
        }

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(set(list_response.json()["results"][0]), expected_keys)
        self.assertEqual(set(detail_response.json()), expected_keys)
        self.assertEqual(detail_response.json()["start_date"], "2026-03-01")
        self.assertNotIn("countdown", detail_response.json())
        self.assertNotIn("modal_id", detail_response.json())

    def test_fast_date_lookups_are_anonymous_and_require_church_and_date(self):
        by_date = self.client.get(f"/api/v1/fasts/by-date/?church_id={self.church.id}&date=2026-03-01")
        by_feast_date = self.client.get(f"/api/v1/fasts/by-feast-date/?church_id={self.church.id}&date=2026-04-05")
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
        response = self.client.get(f"/api/v1/readings/?church_id={self.church.id}&date={target_date.isoformat()}")

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
        response = self.client.get(f"/api/v1/readings/?church_id={self.church.id}&date=2026-03-01")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["readings"],
            [
                {
                    "id": reading.id,
                    "sequence": 1,
                    "book": "Matthew",
                    "start_chapter": 5,
                    "start_verse": 1,
                    "end_chapter": 5,
                    "end_verse": 12,
                }
            ],
        )

    @patch("hub.services.feast_service.get_feast_for_date")
    def test_feast_lookup_is_read_only_and_uses_the_public_schema(self, lookup):
        feast = Feast.objects.create(church=self.church, name="Theophany")
        lookup.return_value = {"name_en": "Theophany"}

        response = self.client.get(f"/api/v1/feasts/?church_id={self.church.id}&date=2026-01-06")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["date"], "2026-01-06")
        self.assertEqual(response.json()["feasts"], [{"id": feast.id, "name": "Theophany", "icon": None}])
        lookup.assert_called_once_with(date(2026, 1, 6), self.church)

    def test_legacy_fast_days_route_is_not_part_of_public_v1(self):
        response = self.client.get(f"/api/v1/fasts/{self.fast.id}/days/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(resolve("/api/v1/fasts/").namespace, "public_api_v1")
        self.assertEqual(reverse("public_api_v1:church-list"), "/api/v1/churches/")

    def resource_requests(self):
        church = {"church_id": self.church.id}
        dated = {**church, "date": "2026-03-01"}
        return [
            ("churches/", {}),
            ("icons/", church),
            ("fasts/", {**church, "start_date": "2026-03-01", "end_date": "2026-03-01"}),
            (f"fasts/{self.fast.id}/", {}),
            ("fasts/by-date/", dated),
            ("fasts/by-feast-date/", dated),
            ("readings/", dated),
            ("feasts/", dated),
        ]

    @patch("hub.services.feast_service.get_feast_for_date", return_value=None)
    def test_every_route_anonymous_stale_auth_and_json_errors(self, lookup):
        for route, params in self.resource_requests():
            url = f"/api/v1/{route}"
            with self.subTest(route=route):
                anonymous = self.client.get(url, params)
                stale = self.client.get(url, params, HTTP_AUTHORIZATION="Bearer stale")
                self.assertEqual(anonymous.status_code, 200)
                self.assertEqual(stale.json(), anonymous.json())
                for method, headers, status, code in (
                    ("post", {}, 405, "method_not_allowed"),
                    ("get", {"HTTP_ACCEPT": "text/html"}, 406, "not_acceptable"),
                ):
                    response = getattr(self.client, method)(url, params, **headers)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response["Content-Type"], "application/json")
                    self.assertEqual(set(response.json()), {"code", "message", "details"})
                    self.assertEqual(response.json()["code"], code)
                    self.assertEqual(response.json()["details"], {})

    def test_invalid_parameters_are_eager_even_with_unknown_church_or_fast(self):
        for route, params in self.resource_requests() + [("fasts/999999/", {})]:
            cases = [] if route in ("churches/", "icons/") else [("lang", ""), ("lang", "invalid")]
            if "church_id" in params:
                cases += [("church_id", ""), ("church_id", "-1")]
            if "date" in params:
                cases += [("date", ""), ("date", "2026-02-30")]
            if route == "fasts/":
                cases += [
                    ("tz", ""),
                    ("tz", "Bad/Zone"),
                    ("start_date", ""),
                    ("end_date", "2026-02-30"),
                    ("start_date", "2027-01-01"),
                ]
            if route in ("churches/", "icons/", "fasts/", "fasts/by-date/", "fasts/by-feast-date/"):
                cases += [("limit", ""), ("limit", "101"), ("offset", ""), ("offset", "-1")]
            for parameter, value in cases:
                invalid = {**params, parameter: value}
                if "church_id" in params and parameter != "church_id":
                    invalid["church_id"] = 999999
                with self.subTest(route=route, parameter=parameter, value=value):
                    with self.assertNumQueries(0):
                        response = self.client.get(f"/api/v1/{route}", invalid)
                    self.assertEqual(response.status_code, 400)

    def test_routes_ignore_parameters_they_do_not_accept(self):
        for route, params in self.resource_requests():
            ignored = {"start_date": "bad", "end_date": "bad", "tz": "bad"} if route != "fasts/" else {}
            if route in ("churches/", "icons/"):
                ignored["lang"] = "invalid"
                ignored["date"] = "bad"
            if route == "churches/" or route == f"fasts/{self.fast.id}/":
                ignored["church_id"] = "bad"
            with self.subTest(route=route):
                response = self.client.get(f"/api/v1/{route}", {**params, **ignored})
                self.assertEqual(response.status_code, 200)

    def test_unknown_church_on_every_church_scoped_route(self):
        for route, params in self.resource_requests():
            if "church_id" not in params:
                continue
            with self.subTest(route=route):
                response = self.client.get(f"/api/v1/{route}", {**params, "church_id": 999999})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["code"], "church_not_found")

    def test_mismatched_day_cannot_leak_fast_membership_or_dates(self):
        Day.objects.create(church=self.other_church, fast=self.fast, date=date(2026, 3, 9))
        Day.objects.create(church=self.other_church, fast=self.fast, date=date(2026, 2, 1))
        for route, params in (
            ("fasts/", {"start_date": "2026-03-09", "end_date": "2026-03-09"}),
            ("fasts/by-date/", {"date": "2026-03-09"}),
        ):
            for church in (self.church, self.other_church):
                response = self.client.get(f"/api/v1/{route}", {**params, "church_id": church.id})
                self.assertEqual(response.json()["results"], [])
        for route, params in self.resource_requests():
            if not route.startswith("fasts/"):
                continue
            params = {**params}
            if route == "fasts/by-feast-date/":
                params["date"] = "2026-04-05"
            response = self.client.get(f"/api/v1/{route}", params)
            data = response.json()
            fasts = data["results"] if "results" in data else [data]
            self.assertEqual(len(fasts), 1)
            self.assertEqual(fasts[0]["start_date"], "2026-03-01")
            self.assertEqual(fasts[0]["end_date"], "2026-03-01")

    @patch("hub.services.feast_service.get_feast_for_date")
    def test_zero_one_two_commemorations_and_no_writes(self, lookup):
        first = Feast.objects.create(church=self.church, name="First")
        second = Feast.objects.create(church=self.church, name="Second")
        Feast.objects.create(church=self.other_church, name="Missing")
        for service_result, expected in (
            (None, []),
            ([], []),
            ({"name_en": "Missing"}, []),
            ({"name_en": "First"}, [first.id]),
            ([{"name_en": "Second"}, {"name_en": "First"}], [second.id, first.id]),
        ):
            lookup.return_value = service_result
            with self.subTest(service_result=service_result):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(
                        "/api/v1/feasts/",
                        {
                            "church_id": self.church.id,
                            "date": "2026-03-01",
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.json()), {"date", "feasts"})
                self.assertEqual([f["id"] for f in response.json()["feasts"]], expected)
                self.assertLessEqual(len(queries), 2)
                self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))

    @patch("hub.services.feast_service.get_feast_for_date")
    def test_invalid_feast_language_never_calls_service_with_no_stored_feast(self, lookup):
        for lang in ("", "invalid"):
            with self.assertNumQueries(0):
                response = self.client.get(
                    "/api/v1/feasts/",
                    {
                        "church_id": self.church.id,
                        "date": "2026-03-01",
                        "lang": lang,
                    },
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], "unsupported_language")
        lookup.assert_not_called()

    def test_pagination_defaults_ordering_links_and_bounds(self):
        Church.objects.bulk_create([Church(name=f"Church {i}") for i in range(30)])
        ids = list(Church.objects.order_by("id").values_list("id", flat=True))
        response = self.client.get("/api/v1/churches/?lang=en")
        data = response.json()
        self.assertEqual(data["count"], len(ids))
        self.assertEqual([row["id"] for row in data["results"]], ids[:25])
        self.assertIsNone(data["previous"])
        self.assertEqual(
            parse_qs(urlparse(data["next"]).query),
            {
                "lang": ["en"],
                "limit": ["25"],
                "offset": ["25"],
            },
        )
        following = self.client.get(data["next"]).json()
        self.assertEqual([row["id"] for row in following["results"]], ids[25:])
        self.assertIsNone(following["next"])
        self.assertEqual(self.client.get(following["previous"]).json(), data)
        self.assertEqual(len(self.client.get("/api/v1/churches/?limit=100").json()["results"]), len(ids))
        self.assertEqual(self.client.get("/api/v1/churches/?offset=10000").json()["results"], [])

    def test_fast_collection_query_count_is_bounded(self):
        for i in range(5):
            fast = Fast.objects.create(church=self.church, name=f"Fast {i}")
            Day.objects.create(church=self.church, fast=fast, date=date(2026, 3, 1))
        with self.assertNumQueries(3):
            response = self.client.get(
                "/api/v1/fasts/by-date/",
                {
                    "church_id": self.church.id,
                    "date": "2026-03-01",
                },
            )
        self.assertEqual(response.json()["count"], 6)

    def test_enabled_registration_preserves_root_and_final_fallback(self):
        from tests.unit.public_api_urls_enabled import enabled

        self.assertEqual(
            [pattern.name for pattern in enabled["urlpatterns"]],
            [
                "root",
                "church-list",
                "icon-list",
                "fast-list",
                "fast-by-date",
                "fast-by-feast-date",
                "fast-detail",
                "reading-by-date",
                "feast-by-date",
                "not-found",
            ],
        )
        self.assertEqual(self.client.get("/api/v1/").status_code, 200)
        response = self.client.get("/api/v1/unknown/nested/", HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    @patch("hub.services.feast_service.get_feast_for_date")
    def test_future_observance_id_lookup_ignores_changed_legacy_name(self, lookup):
        feast = Feast.objects.create(church=self.church, name="Stored name")
        feast.observance_id = "stable-commemoration"
        lookup.return_value = [{"observance_id": feast.observance_id, "name_en": "Changed name"}]
        fields = (*Feast._meta.get_fields(), SimpleNamespace(name="observance_id"))
        with (
            patch.object(Feast._meta, "get_fields", return_value=fields),
            patch.object(Feast.objects, "filter") as stored,
        ):
            stored.return_value.select_related.return_value.order_by.return_value = [feast]
            response = self.client.get(
                "/api/v1/feasts/",
                {
                    "church_id": self.church.id,
                    "date": "2026-03-01",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["feasts"][0]["id"], feast.id)
        stored.assert_called_once_with(church=self.church, observance_id__in=[feast.observance_id])
        stored.return_value.select_related.assert_called_once_with("icon")

    def test_icons_and_readings_are_church_scoped_and_ordered(self):
        own_icon = Icon.objects.create(church=self.church, title="Own")
        Icon.objects.create(church=self.other_church, title="Other")
        response = self.client.get("/api/v1/icons/", {"church_id": self.church.id})
        self.assertEqual([row["id"] for row in response.json()["results"]], [own_icon.id])
        other_day = Day.objects.create(church=self.other_church, date=self.fast_day.date)
        readings = []
        for day, sequence, start_verse in (
            (self.fast_day, 2, 1),
            (other_day, 1, 1),
            (self.fast_day, 1, 3),
        ):
            readings.append(
                Reading.objects.create(
                    day=day,
                    sequence=sequence,
                    book="Matthew",
                    start_chapter=1,
                    start_verse=start_verse,
                    end_chapter=1,
                    end_verse=start_verse + 1,
                )
            )
        with self.assertNumQueries(2):
            response = self.client.get(
                "/api/v1/readings/",
                {
                    "church_id": self.church.id,
                    "date": "2026-03-01",
                },
            )
        self.assertEqual([row["id"] for row in response.json()["readings"]], [readings[2].id, readings[0].id])

    def test_required_parameters_are_rejected_before_queries(self):
        for route, params in self.resource_requests():
            for parameter in ("church_id", "date"):
                if parameter not in params or (route == "icons/" and parameter == "church_id"):
                    continue
                missing = {key: value for key, value in params.items() if key != parameter}
                with self.subTest(route=route, parameter=parameter), self.assertNumQueries(0):
                    response = self.client.get(f"/api/v1/{route}", missing)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["code"], "missing_parameter")
                    self.assertEqual(response.json()["details"], {"parameter": parameter})

    @patch("bahk.public_api.v1.validation.timezone.localdate", return_value=date(2026, 3, 1))
    def test_default_fast_range_and_timezone(self, localdate):
        response = self.client.get(
            "/api/v1/fasts/",
            {
                "church_id": self.church.id,
                "tz": "America/Los_Angeles",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["results"]], [self.fast.id])
        self.assertEqual(str(localdate.call_args.kwargs["timezone"]), "America/Los_Angeles")
