"""Contract tests for public API v1 validation primitives."""

import datetime

import pytz
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from bahk.public_api.v1.validation import (
    PublicApiError,
    PublicApiQuery,
    PublicApiView,
    resource_not_found,
)
from hub.models import Church


class PublicApiQueryTests(SimpleTestCase):
    def query(self, query_string=""):
        return PublicApiQuery(QueryDict(query_string))

    def assert_public_error(self, callable_obj, code, details):
        with self.assertRaises(PublicApiError) as raised:
            callable_obj()
        error = raised.exception
        self.assertEqual(error.public_code, code)
        self.assertEqual(error.public_details, details)

    def test_dates_accept_strict_iso_calendar_dates(self):
        query = self.query("start_date=2026-02-01&end_date=2026-02-28")

        self.assertEqual(
            query.date_range(),
            (datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)),
        )

    def test_invalid_date_never_falls_back_to_default(self):
        self.assert_public_error(
            lambda: self.query("start_date=2026-2-01").date("start_date"),
            "invalid_date",
            {"parameter": "start_date", "value": "2026-2-01"},
        )

    def test_blank_optional_date_is_rejected(self):
        self.assert_public_error(
            lambda: self.query("start_date=").date("start_date"),
            "invalid_date",
            {"parameter": "start_date", "value": ""},
        )

    def test_impossible_calendar_date_is_rejected(self):
        self.assert_public_error(
            lambda: self.query("end_date=2026-02-30").date("end_date"),
            "invalid_date",
            {"parameter": "end_date", "value": "2026-02-30"},
        )

    def test_reversed_date_range_is_rejected(self):
        self.assert_public_error(
            lambda: self.query("start_date=2026-03-01&end_date=2026-02-28").date_range(),
            "invalid_date_range",
            {"start_date": "2026-03-01", "end_date": "2026-02-28"},
        )

    def test_missing_required_church_id_is_rejected(self):
        self.assert_public_error(
            lambda: self.query().church_id(required=True),
            "missing_parameter",
            {"parameter": "church_id"},
        )

    def test_church_id_must_be_a_positive_canonical_integer(self):
        self.assert_public_error(
            lambda: self.query("church_id=01").church_id(),
            "invalid_church_id",
            {"parameter": "church_id", "value": "01"},
        )

    def test_supported_language_is_returned(self):
        self.assertEqual(self.query("lang=hy").language(), "hy")

    def test_unsupported_language_is_rejected(self):
        self.assert_public_error(
            lambda: self.query("lang=fr").language(),
            "unsupported_language",
            {"parameter": "lang", "value": "fr", "supported": ["en", "hy"]},
        )

    def test_blank_language_is_rejected(self):
        self.assert_public_error(
            lambda: self.query("lang=").language(),
            "unsupported_language",
            {"parameter": "lang", "value": "", "supported": ["en", "hy"]},
        )

    def test_timezone_must_be_an_iana_name(self):
        self.assertEqual(self.query("tz=America%2FLos_Angeles").timezone(), pytz.timezone("America/Los_Angeles"))
        self.assert_public_error(
            lambda: self.query("tz=PST").timezone(),
            "invalid_timezone",
            {"parameter": "tz", "value": "PST"},
        )

    def test_blank_timezone_is_rejected(self):
        self.assert_public_error(
            lambda: self.query("tz=").timezone(),
            "invalid_timezone",
            {"parameter": "tz", "value": ""},
        )


class PublicApiChurchQueryTests(TestCase):
    def test_unknown_church_has_a_stable_not_found_error(self):
        query = PublicApiQuery(QueryDict("church_id=999999"))

        with self.assertRaises(PublicApiError) as raised:
            query.church(required=True)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.public_code, "church_not_found")
        self.assertEqual(raised.exception.public_details, {"church_id": 999999})

    def test_known_church_is_resolved(self):
        church = Church.objects.create(name="Public API Church")

        self.assertEqual(
            PublicApiQuery(QueryDict(f"church_id={church.pk}")).church(required=True),
            church,
        )

    def test_unknown_resource_has_a_stable_not_found_error(self):
        error = resource_not_found("fast")

        self.assertEqual(error.status_code, 404)
        self.assertEqual(error.public_code, "resource_not_found")
        self.assertEqual(error.public_details, {"resource": "fast"})


class ErroringPublicView(PublicApiView):
    def get(self, request):
        raise PublicApiError(
            "invalid_date",
            "date must use ISO YYYY-MM-DD format.",
            details={"parameter": "date", "value": "tomorrow"},
        )


class MinimalPublicView(PublicApiView):
    def get(self, request):
        return Response({"anonymous": request.user.is_anonymous, "auth": request.auth})


class PublicApiErrorEnvelopeTests(SimpleTestCase):
    def test_derived_view_ignores_stale_authorization(self):
        request = APIRequestFactory().get("/api/v1/test/", HTTP_AUTHORIZATION="Bearer definitely-not-a-token")
        response = MinimalPublicView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.data, {"anonymous": True, "auth": None})

    def test_derived_view_returns_json_not_acceptable_for_html(self):
        request = APIRequestFactory().get("/api/v1/test/", HTTP_ACCEPT="text/html")
        response = MinimalPublicView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 406)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response.data,
            {
                "code": "not_acceptable",
                "message": "Could not satisfy the request Accept header.",
                "details": {},
            },
        )

    def test_public_api_view_returns_stable_error_envelope(self):
        request = APIRequestFactory().get("/api/v1/test/")
        response = ErroringPublicView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {
                "code": "invalid_date",
                "message": "date must use ISO YYYY-MM-DD format.",
                "details": {"parameter": "date", "value": "tomorrow"},
            },
        )
