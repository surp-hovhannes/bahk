"""Tests for bahk.middleware.SlowRequestLoggingMiddleware (issue #506).

A gateway 504 is invisible from inside the app unless the logs name the endpoint.
These pin the observability contract: slow requests are logged at WARNING with
method, full path, status and duration; fast ones are not; the threshold comes
from settings.
"""

from unittest.mock import Mock

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from bahk.middleware import SlowRequestLoggingMiddleware


def _sleeping_view(seconds):
    def view(request):
        import time

        time.sleep(seconds)
        return HttpResponse("ok", status=200)

    return view


@override_settings(SLOW_REQUEST_THRESHOLD_SECONDS=0.05)
class SlowRequestLoggingMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_slow_request_is_logged_at_warning_with_endpoint_details(self):
        request = self.factory.get("/readings/?date=2025-01-01")
        view = Mock(side_effect=_sleeping_view(0.1))

        with self.assertLogs("bahk.middleware", level="WARNING") as logs:
            response = SlowRequestLoggingMiddleware(view)(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertIn("GET", message)
        self.assertIn("/readings/?date=2025-01-01", message)
        self.assertIn("200", message)
        self.assertIn("threshold", message)

    def test_fast_request_is_not_logged(self):
        request = self.factory.get("/readings/")
        view = Mock(return_value=HttpResponse("ok"))

        with self.assertNoLogs("bahk.middleware"):
            response = SlowRequestLoggingMiddleware(view)(request)

        self.assertEqual(response.status_code, 200)
        view.assert_called_once_with(request)

    def test_threshold_comes_from_settings(self):
        """The same duration crosses the line when the threshold is lowered."""
        request = self.factory.post("/api/v1/fasts/")
        view = Mock(side_effect=_sleeping_view(0.02))

        with override_settings(SLOW_REQUEST_THRESHOLD_SECONDS=0.1):
            with self.assertNoLogs("bahk.middleware"):
                SlowRequestLoggingMiddleware(view)(request)

        with override_settings(SLOW_REQUEST_THRESHOLD_SECONDS=0.01):
            with self.assertLogs("bahk.middleware", level="WARNING") as logs:
                SlowRequestLoggingMiddleware(view)(request)
            self.assertIn("POST", logs.records[0].getMessage())

    def test_response_passes_through_unchanged(self):
        request = self.factory.get("/readings/")
        original = HttpResponse("payload", status=201)
        response = SlowRequestLoggingMiddleware(Mock(return_value=original))(request)

        self.assertIs(response, original)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.content, b"payload")
