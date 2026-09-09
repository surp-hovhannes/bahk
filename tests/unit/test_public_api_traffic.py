"""Public admission, cache, and deployment contracts, including real Redis races."""

import importlib
import io
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import ModuleType
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection as database
from django.http import HttpResponse, QueryDict
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings, tag
from django.urls import include, path
from redis.exceptions import ConnectionError
from rest_framework.exceptions import Throttled

from bahk.public_api import traffic
from bahk.public_api.work import public_request
from bahk.public_api.v1.cache import FINISH, RESERVE
from bahk.public_api.v1.validation import PublicApiError, PublicApiQuery, PublicApiView
from hub.models import Church, Day, Fast


@override_settings(ROOT_URLCONF="tests.unit.public_api_traffic_urls")
class PublicTrafficPolicyTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def identity(self, address, forwarded=""):
        return traffic.client_identity(
            self.factory.get("/api/v1/", REMOTE_ADDR=address, HTTP_X_FORWARDED_FOR=forwarded)
        )

    def test_untrusted_clients_cannot_choose_identity_with_forwarded_headers(self):
        self.assertEqual(self.identity("192.0.2.1"), self.identity("192.0.2.1", "192.0.2.2"))
        self.assertNotEqual(self.identity("192.0.2.1"), self.identity("192.0.2.2"))

    @override_settings(PUBLIC_API_TRUSTED_PROXIES=["10.0.0.0/8"])
    def test_walks_only_the_trusted_right_hand_chain(self):
        self.assertEqual(
            self.identity("192.0.2.1"),
            self.identity("10.0.0.2", "203.0.113.9, 192.0.2.1, 10.0.0.1"),
        )
        with self.assertRaises(ValueError):
            self.identity("10.0.0.2")
        with self.assertRaises(ValueError):
            self.identity("10.0.0.2", "invalid")

    def test_ipv6_addresses_are_normalized(self):
        self.assertEqual(self.identity("2001:db8::1"), self.identity("2001:0db8:0:0:0:0:0:1"))

    @override_settings(PUBLIC_API_TRAFFIC_ENABLED=True)
    @patch.object(traffic, "record")
    @patch.object(traffic, "admit", return_value=12)
    def test_rejected_requests_do_not_execute_inner_middleware(self, admit, record):
        inner = Mock()
        response = traffic.PublicApiTrafficMiddleware(inner)(self.factory.get("/api/v1/churches/"))
        inner.assert_not_called()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "12")
        self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(PUBLIC_API_TRAFFIC_ENABLED=True)
    @patch.object(traffic, "record")
    @patch.object(traffic, "admit", side_effect=ConnectionError)
    def test_limiter_outage_fails_closed(self, admit, record):
        inner = Mock()
        response = traffic.PublicApiTrafficMiddleware(inner)(self.factory.get("/api/v1/"))
        inner.assert_not_called()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "5")

    @patch.object(traffic, "admit")
    def test_product_paths_do_not_consume_public_allowance(self, admit):
        inner = Mock(return_value=HttpResponse())
        for path in ("/api/fasts/", "/hub/", "/api/v10/", "/api/v1-other/"):
            traffic.PublicApiTrafficMiddleware(inner)(self.factory.get(path))
        admit.assert_not_called()

    def test_drf_throttle_preserves_retry_header_and_envelope(self):
        response = PublicApiView().handle_exception(Throttled(wait=2.1))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["details"], {"retry_after": 3})
        self.assertEqual(response["Retry-After"], "3")

    def test_resource_registration_is_off_when_gate_is_disabled(self):
        from bahk.public_api.v1 import urls

        try:
            with override_settings(PUBLIC_API_RESOURCES_ENABLED=False):
                self.assertEqual([p.name for p in importlib.reload(urls).urlpatterns], ["root", "not-found"])
                urlconf = ModuleType("public_api_gate_test")
                urlconf.urlpatterns = [path("api/v1/", include(urls.urlpatterns))]
                with override_settings(ROOT_URLCONF=urlconf):
                    self.assertEqual(self.client.get("/api/v1/").status_code, 200)
                    response = self.client.get("/api/v1/churches/")
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["code"], "not_found")
        finally:
            importlib.reload(urls)

    @patch("bahk.public_api.v1.validation.timezone.localdate", return_value=date(2026, 3, 1))
    def test_effective_range_checks_defaults_and_inclusive_limit(self, today):
        start, end = PublicApiQuery(QueryDict()).effective_date_range()
        self.assertEqual((end - start).days + 1, 361)
        for params in (
            "start_date=2020-01-01",
            "end_date=2020-01-01",
            "start_date=2024-01-01&end_date=2025-01-01",
        ):
            with self.subTest(params=params), self.assertRaises(PublicApiError):
                PublicApiQuery(QueryDict(params)).effective_date_range()
        PublicApiQuery(QueryDict("start_date=2024-01-01&end_date=2024-12-31")).effective_date_range()

    def test_oversized_pagination_and_church_ids_return_400(self):
        for path in (
            "/api/v1/churches/?offset=10001",
            "/api/v1/churches/?offset=" + "9" * 5000,
            "/api/v1/icons/?church_id=9223372036854775808",
        ):
            with self.subTest(path=path[:80]):
                self.assertEqual(self.client.get(path).status_code, 400)

    @override_settings(PUBLIC_API_METRICS_TOKEN="secret-token")
    def test_metrics_require_the_separate_bearer_token(self):
        for authorization in ("", "Bearer other", "secret-token"):
            response = self.client.get("/internal/public-api-metrics/", HTTP_AUTHORIZATION=authorization)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(PUBLIC_API_METRICS_TOKEN="secret-token")
    @patch.object(traffic, "connection", side_effect=ConnectionError)
    def test_metrics_report_store_failure_even_when_request_counters_cannot_write(self, connection):
        response = self.client.get("/internal/public-api-metrics/", HTTP_AUTHORIZATION="Bearer secret-token")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"public_api_store_up 0\n", response.content)
        self.assertIn(b"# TYPE public_api_store_up gauge", response.content)


@override_settings(
    PUBLIC_API_TRAFFIC_ENABLED=True,
    PUBLIC_API_RESPONSE_CACHE_ENABLED=True,
    PUBLIC_API_REDIS_URL="redis://public-redis:6379/0",
    PUBLIC_API_METRICS_TOKEN="a" * 32,
)
class PublicReadinessTests(SimpleTestCase):
    @patch("hub.management.commands.check_public_api_readiness.connection")
    def test_checks_configuration_without_writes(self, connection):
        client = connection.return_value
        client.config_get.return_value = {"maxmemory-policy": "noeviction", "maxmemory": 100000000}
        output = io.StringIO()
        call_command("check_public_api_readiness", stdout=output)
        self.assertIn("checks passed", output.getvalue())
        self.assertEqual([call[0] for call in client.mock_calls], ["config_get", "ping"])

    @override_settings(REDIS_URL="redis://public-redis:6379/1")
    def test_different_logical_database_is_not_isolation(self):
        with self.assertRaisesMessage(CommandError, "isolated"):
            call_command("check_public_api_readiness")

    @patch("hub.management.commands.check_public_api_readiness.connection")
    def test_eviction_and_unbounded_memory_block_readiness(self, connection):
        connection.return_value.config_get.return_value = {"maxmemory-policy": "allkeys-lru", "maxmemory": 0}
        with self.assertRaisesMessage(CommandError, "noeviction"):
            call_command("check_public_api_readiness")


@override_settings(ROOT_URLCONF="tests.unit.public_api_traffic_urls")
class PublicReadBoundaryTests(TestCase):
    def test_session_and_bearer_credentials_cannot_write_profiles_or_events(self):
        from rest_framework_simplejwt.tokens import AccessToken

        user = get_user_model().objects.create_user(username="public-reader", password="test")
        token = str(AccessToken.for_user(user))
        self.client.force_login(user)

        def reject_writes(execute, sql, params, many, context):
            self.assertNotIn(sql.lstrip().split()[0].upper(), {"INSERT", "UPDATE", "DELETE"})
            return execute(sql, params, many, context)

        with (
            database.execute_wrapper(reject_writes),
            patch(
                "events.middleware.AnalyticsTrackingMiddleware._get_authenticated_user",
                side_effect=AssertionError("Public requests must not authenticate for analytics"),
            ),
        ):
            for headers in ({}, {"HTTP_AUTHORIZATION": f"Bearer {token}"}):
                response = self.client.get("/api/v1/churches/?tz=Europe/Paris&utm_source=public", **headers)
                self.assertEqual(response.status_code, 200)


class PublicCostBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/api/v1/")
        token = public_request.set(self.request)
        self.addCleanup(public_request.reset, token)

    def test_llm_providers_are_blocked_before_network_work(self):
        from hub.services.llm_requests import anthropic_message, openai_chat_completion

        client = Mock()
        for call in (anthropic_message, openai_chat_completion):
            with self.assertRaises(RuntimeError):
                call(client, model="test-model", messages=[], max_tokens=1)
        client.messages.create.assert_not_called()
        client.chat.completions.create.assert_not_called()
        self.assertEqual(self.request.public_blocked_work, {"llm": 2})

    def test_passage_fetch_is_blocked_before_network_work(self):
        from hub.services.bible_api_service import BibleAPIService

        service = BibleAPIService(api_key="test-key")
        service.session = Mock()
        with self.assertRaises(RuntimeError):
            service.get_passage("GEN", 1, 1, 1, 2)
        service.session.get.assert_not_called()

    def test_direct_and_eager_task_dispatch_are_blocked(self):
        from celery import shared_task

        from bahk.celery import PublicReadSafeTask, app

        body = Mock(return_value=42)

        @shared_task(name="test.public.forbidden", lazy=False)
        def forbidden():
            return body()

        self.addCleanup(app.tasks.pop, forbidden.name, None)
        self.assertIsInstance(forbidden, PublicReadSafeTask)

        with self.assertRaises(RuntimeError):
            app.send_task("test.public.forbidden")
        for invoke in (forbidden.delay, forbidden.apply_async, forbidden.apply, forbidden):
            with self.subTest(invoke=invoke), self.assertRaises(RuntimeError):
                invoke()
        body.assert_not_called()
        self.assertEqual(self.request.public_blocked_work, {"task": 5})

        token = public_request.set(None)
        try:
            self.assertEqual(forbidden(), 42)
            self.assertEqual(forbidden.apply().get(), 42)
            with patch("celery.app.task.Task.apply_async", return_value="queued") as dispatch:
                self.assertEqual(forbidden.delay(), "queued")
                self.assertEqual(forbidden.apply_async(), "queued")
                self.assertEqual(dispatch.call_count, 2)
        finally:
            public_request.reset(token)

    def test_guard_is_context_local(self):
        from bahk.public_api.work import reject_public_work

        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertIsNone(pool.submit(reject_public_work, "task").result())


class PublicContextCleanupTests(SimpleTestCase):
    def test_middleware_resets_context_after_an_exception(self):
        def fail(request):
            self.assertIs(public_request.get(), request)
            raise RuntimeError("test error")

        with self.assertRaises(RuntimeError):
            traffic.PublicApiTrafficMiddleware(fail)(RequestFactory().get("/api/v1/"))
        self.assertIsNone(public_request.get())

    def test_unhandled_public_failure_does_not_expose_an_html_debug_page(self):
        inner = Mock(return_value=HttpResponse("sensitive debug page", status=500))
        response = traffic.PublicApiTrafficMiddleware(inner)(RequestFactory().get("/api/v1/"))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"sensitive", response.content)
        self.assertEqual(response["Retry-After"], "5")


REDIS_URL = os.environ.get("PUBLIC_API_TEST_REDIS_URL")


class RedisIsolation:
    def setUp(self):
        super().setUp()
        self.prefix = "bahk:test:public:" + uuid.uuid4().hex
        overrides = override_settings(
            ROOT_URLCONF="tests.unit.public_api_traffic_urls",
            PUBLIC_API_REDIS_URL=REDIS_URL,
            PUBLIC_API_REDIS_PREFIX=self.prefix,
            PUBLIC_API_TRAFFIC_ENABLED=True,
            PUBLIC_API_RESPONSE_CACHE_ENABLED=True,
            PUBLIC_API_RATE_MINUTE=1000,
            PUBLIC_API_RATE_HOUR=10000,
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.redis = traffic.connection()
        self.redis.ping()  # Explicit opt-in makes a missing Redis a failure, not a skip.
        self.addCleanup(self.cleanup_redis)

    def cleanup_redis(self):
        keys = list(self.redis.scan_iter(match=f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)


@skipUnless(REDIS_URL, "Set PUBLIC_API_TEST_REDIS_URL for real-Redis integration checks")
class PublicRedisAdmissionTests(RedisIsolation, SimpleTestCase):
    @override_settings(PUBLIC_API_RATE_MINUTE=7, PUBLIC_API_RATE_HOUR=7)
    def test_concurrent_workers_share_one_allowance(self):
        request = RequestFactory().get("/api/v1/", REMOTE_ADDR="192.0.2.1")
        with ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(lambda _: traffic.admit(request), range(40)))
        self.assertEqual(outcomes.count(0), 7)
        self.assertTrue(all(value >= 0 for value in outcomes))

    def test_window_resets_and_retry_wait_for_all_limits(self):
        def attempt(now):
            script = traffic.ADMIT.replace("tonumber(redis.call('TIME')[1])", str(now))
            return self.redis.eval(script, 1, self.prefix + ":boundary", 1, 2)

        self.assertEqual(attempt(3590), 0)
        self.assertEqual(attempt(3591), 9)
        self.assertEqual(attempt(3600), 0)
        self.assertEqual(attempt(3660), 0)
        self.assertEqual(attempt(3720), 3480)
        for key in self.redis.scan_iter(match=f"{self.prefix}:boundary:*"):
            self.assertGreater(self.redis.ttl(key), 0)

    @override_settings(PUBLIC_API_RATE_MINUTE=1, CORS_ALLOW_ALL_ORIGINS=True)
    def test_route_and_method_allowance_and_cors_retry_header(self):
        self.assertEqual(self.client.head("/api/v1/").status_code, 200)
        response = self.client.get("/api/v1/churches/", HTTP_ORIGIN="https://consumer.test")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "throttled")
        self.assertEqual(response.json()["details"]["retry_after"], int(response["Retry-After"]))
        self.assertIn("retry-after", response["Access-Control-Expose-Headers"].lower())

    def test_metrics_are_scrapable_and_have_no_request_secrets(self):
        self.client.get("/api/v1/?secret=never-export-this", HTTP_AUTHORIZATION="Bearer private")
        with override_settings(PUBLIC_API_METRICS_TOKEN="metrics-only"):
            response = self.client.get("/internal/public-api-metrics/", HTTP_AUTHORIZATION="Bearer metrics-only")
        text = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('public_api_requests_total{route="root",status="200"} 1.0', text)
        self.assertIn("public_api_store_up 1", text)
        self.assertNotIn("never-export-this", text)
        self.assertNotIn("private", text)

    def test_blocked_work_is_recorded_from_the_full_request_stack(self):
        from bahk.public_api.v1.views import PublicApiRootView
        from bahk.public_api.work import reject_public_work

        def forbidden(view, request, *args, **kwargs):
            reject_public_work("llm")

        self.client.raise_request_exception = False
        with patch.object(PublicApiRootView, "get", forbidden):
            response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "service_unavailable")
        self.assertIsNone(public_request.get())
        self.assertEqual(self.redis.hget(f"{self.prefix}:metrics", 'blocked_work_total{kind="llm"}'), "1")

    def test_cache_reservations_bound_concurrency_and_capacity(self):
        keys = [self.prefix + ":responses:index", self.prefix + ":entry", self.prefix + ":lease"]

        def reserve(token):
            return self.redis.eval(RESERVE, 3, *keys, 1, token, 30, 300)[0]

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve, map(str, range(20))))
        self.assertEqual(outcomes.count("miss"), 1)
        self.assertEqual(outcomes.count("busy"), 19)
        other = [keys[0], self.prefix + ":other", self.prefix + ":other:lease"]
        self.assertEqual(self.redis.eval(RESERVE, 3, *other, 1, "other", 30, 300)[0], "full")
        token = self.redis.get(keys[2])
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, "wrong-owner", "{}", 300), 0)
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, token, "{}", 300), 1)
        self.assertEqual(reserve("next"), "hit")
        self.assertGreater(self.redis.ttl(keys[1]), 0)
        # Simulate expiration without sleeping or touching unrelated data.
        self.redis.delete(keys[1])
        self.redis.zadd(keys[0], {keys[1]: 1})
        self.assertEqual(self.redis.eval(RESERVE, 3, *other, 1, "other", 30, 300)[0], "miss")


@skipUnless(REDIS_URL, "Set PUBLIC_API_TEST_REDIS_URL for real-Redis integration checks")
class PublicRedisResponseTests(RedisIsolation, TestCase):
    def setUp(self):
        super().setUp()
        self.church = Church.objects.create(name="Cache Church")
        Church.objects.create(name="Second Cache Church")
        self.fast = Fast.objects.create(church=self.church, name="Cache Fast")
        Day.objects.create(church=self.church, fast=self.fast, date=date(2026, 3, 1))

    def test_equivalent_requests_reuse_data_but_rebuild_pagination_links(self):
        first = self.client.get("/api/v1/churches/?limit=1&tracking=one")
        with self.assertNumQueries(0):
            second = self.client.get("/api/v1/churches/?tracking=two&limit=01")
        self.assertEqual(first.json()["results"], second.json()["results"])
        self.assertIn("tracking=two", second.json()["next"])
        self.assertNotIn("tracking=one", second.json()["next"])
        self.assertEqual(second["Cache-Control"], "no-store")

    def test_cached_data_does_not_bypass_validation(self):
        self.client.get("/api/v1/churches/")
        for query in ("limit=0", "offset=10001"):
            self.assertEqual(self.client.get("/api/v1/churches/?" + query).status_code, 400)

    def test_ignored_language_reuses_canonical_resource_cache(self):
        for route in ("churches", "icons"):
            first = self.client.get(f"/api/v1/{route}/")
            with self.assertNumQueries(0):
                second = self.client.get(f"/api/v1/{route}/?lang=invalid", HTTP_ACCEPT_LANGUAGE="hy")
            self.assertEqual(second.status_code, 200)
            self.assertEqual(first.json(), second.json())
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 2)

    def test_localized_cache_hit_still_validates_language(self):
        url = f"/api/v1/fasts/{self.fast.pk}/"
        self.assertEqual(self.client.get(url).status_code, 200)
        with self.assertNumQueries(0):
            response = self.client.get(url + "?lang=invalid")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "unsupported_language")

    def test_cache_hits_still_consume_allowance(self):
        with override_settings(PUBLIC_API_RATE_MINUTE=2):
            for expected in (200, 200, 429):
                self.assertEqual(self.client.get("/api/v1/churches/").status_code, expected)

    def test_cached_response_is_stale_only_until_its_ttl(self):
        self.client.get("/api/v1/churches/")
        Church.objects.filter(pk=self.church.pk).update(name="Renamed")
        before = self.client.get("/api/v1/churches/").json()
        self.assertIn("Cache Church", [row["name"] for row in before["results"]])
        for key in self.redis.scan_iter(match=f"{self.prefix}:responses:v2:*"):
            self.redis.expire(key, 0)
        after = self.client.get("/api/v1/churches/").json()
        self.assertIn("Renamed", [row["name"] for row in after["results"]])

    def test_oversized_payload_is_not_cached(self):
        with override_settings(PUBLIC_API_CACHE_MAX_BYTES=1):
            self.assertEqual(self.client.get("/api/v1/churches/").status_code, 200)
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 0)

    def test_errors_release_the_fill_reservation(self):
        for _ in range(2):
            self.assertEqual(self.client.get("/api/v1/fasts/999999/").status_code, 404)
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 0)

    def test_effective_language_is_part_of_the_key(self):
        self.client.get(f"/api/v1/fasts/{self.fast.pk}/", HTTP_ACCEPT_LANGUAGE="en")
        self.client.get(f"/api/v1/fasts/{self.fast.pk}/", HTTP_ACCEPT_LANGUAGE="hy")
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 2)

    @patch("bahk.public_api.v1.validation.timezone.localdate")
    def test_default_date_cache_changes_at_midnight(self, today):
        url = f"/api/v1/fasts/?church_id={self.church.pk}"
        today.return_value = date(2026, 3, 1)
        self.assertEqual(self.client.get(url).status_code, 200)
        today.return_value = date(2026, 3, 2)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 2)

    def test_full_cache_still_serves_bounded_reads(self):
        with override_settings(PUBLIC_API_CACHE_MAX_ENTRIES=1):
            self.assertEqual(self.client.get("/api/v1/churches/").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/icons/").status_code, 200)
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 1)

    def test_response_cache_outage_does_not_bypass_admission(self):
        with patch("bahk.public_api.v1.cache.connection", side_effect=ConnectionError):
            response = self.client.get("/api/v1/churches/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('cache_total{outcome="bypass"}', self.redis.hgetall(f"{self.prefix}:metrics"))

    def test_busy_fill_returns_uncached_retry_response(self):
        client = Mock()
        client.eval.return_value = ["busy", ""]
        with patch("bahk.public_api.v1.cache.connection", return_value=client):
            with self.assertNumQueries(0):
                response = self.client.get("/api/v1/churches/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(response["Cache-Control"], "no-store")

    @patch("bahk.public_api.v1.validation.timezone.localdate", return_value=date(2026, 3, 1))
    def test_default_dates_and_explicit_dates_share_a_cache_entry(self, today):
        prefix = f"/api/v1/fasts/?church_id={self.church.pk}"
        self.assertEqual(self.client.get(prefix).status_code, 200)
        with self.assertNumQueries(0):
            response = self.client.get(prefix + "&start_date=2025-09-02&end_date=2026-08-28")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 1)

    @tag("performance")
    def test_bounded_warm_and_diverse_query_work(self):
        self.client.get("/api/v1/churches/?limit=1")
        durations = []
        with self.assertNumQueries(0):
            for number in range(100):
                started = time.perf_counter()
                response = self.client.get(f"/api/v1/churches/?limit=1&ignored={number}")
                durations.append(time.perf_counter() - started)
                self.assertEqual(response.status_code, 200)
        with override_settings(PUBLIC_API_CACHE_MAX_ENTRIES=4):
            for offset in range(1, 26):
                # A count and at most one page query; no work proportional to range length.
                with database.execute_wrapper(self.count_queries):
                    self.query_count = 0
                    response = self.client.get(f"/api/v1/churches/?limit=1&offset={offset}")
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(self.query_count, 2)
        self.assertLessEqual(self.redis.zcard(f"{self.prefix}:responses:index"), 4)
        print(f"Public cache: 100 warm requests, zero SQL queries, p95={sorted(durations)[94]:.4f}s")

    def count_queries(self, execute, sql, params, many, context):
        self.query_count += 1
        return execute(sql, params, many, context)
