"""Golden combined-day contract, read boundaries, and canonical cache identity."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import DatabaseError, connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from rest_framework.request import Request

from bahk.public_api.v1.cache import canonical_parameters
from bahk.public_api.v1.resource_views import CalendarDayView
from bahk.public_api.v1.serializers import FeastPublicSerializer
from hub.models import Church, Day, Fast, Feast, Reading
from hub.services import feast_service
from icons.models import Icon


class PendingFeastDataUnavailable(RuntimeError):
    """Stand-in for the explicit exception exported by the pending feast service."""


@override_settings(
    ROOT_URLCONF="tests.unit.public_api_urls_enabled",
    PUBLIC_API_TRAFFIC_ENABLED=False,
    PUBLIC_API_RESPONSE_CACHE_ENABLED=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
)
class PublicCalendarTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Calendar Church")
        self.other = Church.objects.create(name="Other Calendar Church")
        self.params = {"church_id": self.church.pk, "date": "2026-03-01"}
        self.lookup = self.enterContext(patch.object(feast_service, "get_feast_for_date", return_value=[]))

    def get(self, **params):
        return self.client.get("/api/v1/calendar/", {**self.params, **params})

    def populate(self, church):
        fast = Fast.objects.create(church=church, name="Great Fast", i18n={"name_hy": "Մեծ պահք"})
        day = Day.objects.create(church=church, fast=fast, date=date(2026, 3, 1))
        reading = Reading.objects.create(
            day=day,
            sequence=1,
            book="Matthew",
            start_chapter=5,
            start_verse=1,
            end_chapter=5,
            end_verse=12,
            i18n={"book_hy": "Մատթեոս"},
        )
        feast = Feast.objects.create(church=church, name="First", i18n={"name_hy": "Առաջին"})
        return fast, reading, feast

    def test_exact_empty_contract_and_no_day_creation(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "date": "2026-03-01",
                "church": {"id": self.church.pk, "name": self.church.name},
                "readings": [],
                "fast": None,
                "feasts": [],
                "partial_failures": [],
            },
        )
        self.assertEqual(len(queries), 3)
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))
        self.assertFalse(Day.objects.filter(church=self.church).exists())

    def test_exact_localized_contract_and_select_only_budget(self):
        fast, reading, feast = self.populate(self.church)
        self.lookup.return_value = {"name_en": feast.name}
        for lang, fast_name, book, feast_name in (
            ("en", "Great Fast", "Matthew", "First"),
            ("hy", "Մեծ պահք", "Մատթեոս", "Առաջին"),
        ):
            with self.subTest(lang=lang), CaptureQueriesContext(connection) as queries:
                response = self.get(lang=lang)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {
                    "date": "2026-03-01",
                    "church": {"id": self.church.pk, "name": self.church.name},
                    "readings": [
                        {
                            "id": reading.pk,
                            "sequence": 1,
                            "book": book,
                            "start_chapter": 5,
                            "start_verse": 1,
                            "end_chapter": 5,
                            "end_verse": 12,
                        }
                    ],
                    "fast": {
                        "id": fast.pk,
                        "church_id": self.church.pk,
                        "name": fast_name,
                        "description": None,
                        "start_date": "2026-03-01",
                        "end_date": "2026-03-01",
                        "culmination_feast": None,
                        "culmination_feast_date": None,
                        "year": None,
                        "image_url": None,
                        "thumbnail_url": None,
                        "learn_more_url": None,
                    },
                    "feasts": [{"id": feast.pk, "name": feast_name, "icon": None}],
                    "partial_failures": [],
                },
            )
            self.assertEqual(len(queries), 4)
            self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))

    def test_two_churches_same_date_and_malformed_fast_membership(self):
        own_fast, own_reading, own_feast = self.populate(self.church)
        other_fast, other_reading, other_feast = self.populate(self.other)
        self.lookup.return_value = [{"name_en": "First"}]
        Day.objects.create(church=self.other, fast=own_fast, date=date(2026, 2, 1))
        Day.objects.create(church=self.other, fast=own_fast, date=date(2026, 3, 1))
        Day.objects.create(church=self.church, fast=own_fast, date=date(2026, 3, 3))
        for church, fast, reading, feast, end in (
            (self.church, own_fast, own_reading, own_feast, "2026-03-03"),
            (self.other, other_fast, other_reading, other_feast, "2026-03-01"),
        ):
            data = self.get(church_id=church.pk).json()
            self.assertEqual(data["church"], {"id": church.pk, "name": church.name})
            self.assertEqual(data["fast"]["id"], fast.pk)
            self.assertEqual(data["fast"]["start_date"], "2026-03-01")
            self.assertEqual(data["fast"]["end_date"], end)
            self.assertEqual([r["id"] for r in data["readings"]], [reading.pk])
            self.assertEqual([f["id"] for f in data["feasts"]], [feast.pk])
        self.assertIsNone(self.get(church_id=self.other.pk, date="2026-02-01").json()["fast"])

    def test_multiple_fast_candidates_choose_lowest_id_and_readings_keep_order(self):
        first, reading, _ = self.populate(self.church)
        second = Fast.objects.create(church=self.church, name="Second Fast")
        Day.objects.create(church=self.church, fast=second, date=date(2026, 3, 1))
        earlier = Reading.objects.create(
            day=reading.day, sequence=0, book="John", start_chapter=1, start_verse=1, end_chapter=1, end_verse=2
        )
        for _ in range(2):
            data = self.get().json()
            self.assertEqual(data["fast"]["id"], min(first.pk, second.pk))
            self.assertEqual([r["id"] for r in data["readings"]], [earlier.pk, reading.pk])

    def test_zero_one_two_commemorations_and_nested_icon_without_extra_queries(self):
        first = Feast.objects.create(church=self.church, name="First")
        icon = Icon.objects.create(church=self.church, title="Stored icon")
        second = Feast.objects.create(church=self.church, name="Second", icon=icon)
        for result, expected in (
            (None, []),
            ([], []),
            ({"name_en": "Missing"}, []),
            ({"name_en": "First"}, [{"id": first.pk, "name": "First", "icon": None}]),
            (
                [{"name_en": "Second"}, {"name_en": "First"}, {"name_en": "Second"}],
                [
                    {
                        "id": second.pk,
                        "name": "Second",
                        "icon": {"id": icon.pk, "title": icon.title, "image_url": None, "thumbnail_url": None},
                    },
                    {"id": first.pk, "name": "First", "icon": None},
                ],
            ),
        ):
            self.lookup.return_value = result
            with self.subTest(result=result), CaptureQueriesContext(connection) as queries:
                response = self.get()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["feasts"], expected)
            self.assertEqual(response.json()["partial_failures"], [])
            self.assertLessEqual(len(queries), 4)
            self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))

    def test_pending_observance_ids_reuse_the_feast_route_lookup(self):
        feast = Feast.objects.create(church=self.church, name="Stored name")
        feast.observance_id = "stable-id"
        self.lookup.return_value = [{"observance_id": "stable-id", "name_en": "Changed name"}]
        fields = (*Feast._meta.get_fields(), SimpleNamespace(name="observance_id"))
        with (
            patch.object(Feast._meta, "get_fields", return_value=fields),
            patch.object(Feast.objects, "filter") as stored,
        ):
            stored.return_value.select_related.return_value.order_by.return_value = [feast]
            data = self.get().json()
        self.assertEqual(data["feasts"], [{"id": feast.pk, "name": "Stored name", "icon": None}])
        stored.assert_called_once_with(church=self.church, observance_id__in=["stable-id"])

    def test_legacy_only_results_after_observance_field_exists(self):
        first = Feast.objects.create(church=self.church, name="First")
        second = Feast.objects.create(church=self.church, name="Second")
        self.lookup.return_value = [{"name": "Second"}, {"name_en": "First"}, {"name": "Second"}]
        fields = (*Feast._meta.get_fields(), SimpleNamespace(name="observance_id"))
        with patch.object(Feast._meta, "get_fields", return_value=fields):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["feasts"]], [second.pk, first.pk])

    def test_mixed_transition_results_preserve_order_and_deduplicate_rows(self):
        first = Feast.objects.create(church=self.church, name="First")
        second = Feast.objects.create(church=self.church, name="Second")
        Feast.objects.create(church=self.church, name="Must not match by name")
        first.observance_id = "first-id"
        self.lookup.return_value = [
            {"name": "Second"},
            {"observance_id": "first-id", "name_en": "Changed name"},
            {"name_en": "First"},
            {"observance_id": "first-id"},
            {"observance_id": "missing-id", "name_en": "Must not match by name"},
        ]
        fields = (*Feast._meta.get_fields(), SimpleNamespace(name="observance_id"))
        original_filter = Feast.objects.filter

        def stored_feasts(**kwargs):
            if "observance_id__in" in kwargs:
                self.assertEqual(kwargs, {"church": self.church, "observance_id__in": ["first-id", "missing-id"]})
                result = Mock()
                result.select_related.return_value.order_by.return_value = [first]
                return result
            return original_filter(**kwargs)

        with (
            patch.object(Feast._meta, "get_fields", return_value=fields),
            patch.object(Feast.objects, "filter", side_effect=stored_feasts),
        ):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["feasts"]], [second.pk, first.pk])

    def test_cross_church_nested_icon_is_null_without_mutation(self):
        icon = Icon.objects.create(church=self.other, title="Other church private icon")
        feast = Feast.objects.create(church=self.church, name="First", icon=icon)
        self.lookup.return_value = {"name_en": feast.name}
        with self.assertNumQueries(0):
            self.assertIsNone(FeastPublicSerializer(feast).data["icon"])
            self.assertIs(feast.icon, icon)
            self.assertEqual(feast.icon_id, icon.pk)
        for route in ("/api/v1/calendar/", "/api/v1/feasts/"):
            with self.subTest(route=route), CaptureQueriesContext(connection) as queries:
                response = self.client.get(route, self.params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["feasts"], [{"id": feast.pk, "name": feast.name, "icon": None}])
            self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))
        feast.refresh_from_db()
        self.assertEqual(feast.icon_id, icon.pk)

    def test_only_explicit_pending_unavailability_is_partial(self):
        self.populate(self.church)
        self.lookup.side_effect = PendingFeastDataUnavailable("private engine details")
        with patch.object(feast_service, "FeastDataUnavailable", PendingFeastDataUnavailable, create=True):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["feasts"], [])
        self.assertEqual(data["partial_failures"], [{"component": "feasts", "code": "data_unavailable"}])
        self.assertIsInstance(data["partial_failures"], list)
        for failure in data["partial_failures"]:
            self.assertEqual(set(failure), {"component", "code"})
            self.assertEqual((failure["component"], failure["code"]), ("feasts", "data_unavailable"))
        self.assertIsNotNone(data["fast"])
        self.assertEqual(len(data["readings"]), 1)
        self.assertNotIn("private", response.content.decode())

    def test_unexpected_and_database_failures_fail_closed(self):
        self.client.raise_request_exception = False
        for error in (DatabaseError("private database details"), RuntimeError("private unexpected details")):
            self.lookup.side_effect = error
            with self.subTest(error=error):
                response = self.get()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "service_unavailable")
                self.assertNotIn("private", response.content.decode())
        self.lookup.side_effect = None
        with patch.object(Reading.objects, "filter", side_effect=DatabaseError("private")):
            self.assertEqual(self.get().status_code, 503)

    def test_invalid_or_missing_inputs_never_query_or_call_service(self):
        for parameter, values in {
            "date": (None, "", "2026-02-30", "2026-3-1"),
            "church_id": (None, "", "0", "-1", "01", "abc"),
            "lang": ("", "invalid"),
            "tz": ("", "Bad/Zone"),
        }.items():
            for value in values:
                params = {**self.params, "church_id": 999999, parameter: value}
                if value is None:
                    params.pop(parameter)
                with self.subTest(parameter=parameter, value=value), self.assertNumQueries(0):
                    response = self.client.get("/api/v1/calendar/", params)
                    self.assertEqual(response.status_code, 400)
        self.lookup.assert_not_called()
        self.assertEqual(self.get(church_id=999999).status_code, 404)
        self.lookup.assert_not_called()

    def test_canonical_parameters_pin_all_defaults_and_isolate_each_dimension(self):
        def canonical(params, active="en"):
            view = CalendarDayView()
            view.request = Request(RequestFactory().get("/api/v1/calendar/", params))
            view.kwargs = {}
            with translation.override(active):
                return canonical_parameters(view, view.request)

        expected = {"church_id": self.church.pk, "date": "2026-03-01", "lang": "en", "tz": "UTC"}
        with self.assertNumQueries(0):
            self.assertEqual(canonical(self.params), expected)
            self.assertEqual(canonical({**self.params, "lang": "en", "tz": "UTC", "ignored": "x"}), expected)
            self.assertEqual(canonical(self.params, "hy"), {**expected, "lang": "hy"})
            for key, value in (
                ("church_id", self.other.pk),
                ("date", "2026-03-02"),
                ("lang", "hy"),
                ("tz", "Asia/Yerevan"),
            ):
                self.assertEqual(canonical({**self.params, key: value}), {**expected, key: value})
        self.lookup.assert_not_called()

    def test_method_and_accept_errors_are_json_before_resource_work(self):
        for method, headers, status, code in (
            ("post", {}, 405, "method_not_allowed"),
            ("put", {}, 405, "method_not_allowed"),
            ("patch", {}, 405, "method_not_allowed"),
            ("delete", {}, 405, "method_not_allowed"),
            ("get", {"HTTP_ACCEPT": "text/html"}, 406, "not_acceptable"),
        ):
            with self.subTest(method=method, headers=headers), self.assertNumQueries(0):
                response = getattr(self.client, method)("/api/v1/calendar/", **headers)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertEqual(set(response.json()), {"code", "message", "details"})
            self.assertEqual(response.json()["code"], code)
            self.assertEqual(response.json()["details"], {})
        self.lookup.assert_not_called()

    def test_request_locale_fallback_and_explicit_language_override(self):
        self.populate(self.church)
        response = self.client.get("/api/v1/calendar/", self.params, HTTP_ACCEPT_LANGUAGE="hy")
        self.assertEqual(response.json()["fast"]["name"], "Մեծ պահք")
        response = self.client.get("/api/v1/calendar/", {**self.params, "lang": "en"}, HTTP_ACCEPT_LANGUAGE="hy")
        self.assertEqual(response.json()["fast"]["name"], "Great Fast")
        self.assertEqual(response.json()["fast"]["description"], None)

    def test_timezone_does_not_shift_explicit_calendar_date_and_head_is_supported(self):
        self.populate(self.church)
        self.assertEqual(self.get(tz="Asia/Yerevan").json(), self.get(tz="UTC").json())
        response = self.client.head("/api/v1/calendar/", self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
