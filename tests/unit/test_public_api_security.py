"""Activation settings and slow cache-fill regression tests."""

import ast
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from decouple import Config, Csv, RepositoryEmpty
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from rest_framework.response import Response

from bahk.public_api.v1.cache import FINISH, RENEW, RESERVE, FillHeartbeat, cached_public_get
from bahk.public_api.v1.validation import PublicApiError
from tests.unit.test_public_api_traffic import REDIS_URL, RedisIsolation


class PublicActivationSettingsTests(SimpleTestCase):
    def settings_block(self, **environment):
        # Execute the actual public settings block without unrelated integrations,
        # dotenv files, network work, or mutating Django's live settings module.
        source = Path(__file__).resolve().parents[2] / "bahk" / "settings.py"
        tree = ast.parse(source.read_text())
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id.startswith("PUBLIC_API_") for target in node.targets
            ):
                nodes.append(node)
            elif isinstance(node, ast.If) and any(
                isinstance(child, ast.Name) and child.id.startswith("PUBLIC_API_") for child in ast.walk(node.test)
            ):
                nodes.append(node)
        scope = {"config": Config(RepositoryEmpty()), "Csv": Csv, "ImproperlyConfigured": ImproperlyConfigured,
                 "REDIS_URL": "redis://existing-redis:6379/0"}
        with patch.dict("os.environ", environment, clear=True):
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
        return scope

    def test_defaults_remain_disabled(self):
        values = self.settings_block()
        self.assertIs(values["PUBLIC_API_RESOURCES_ENABLED"], False)
        self.assertIs(values["PUBLIC_API_DEPLOYMENT_READY"], False)
        self.assertIs(values['PUBLIC_API_RESPONSE_CACHE_ENABLED'], False)

    def test_shared_mode_uses_existing_store_without_response_cache(self):
        values = self.settings_block(PUBLIC_API_REDIS_MODE='shared')
        self.assertEqual(values['PUBLIC_API_REDIS_URL'], 'redis://existing-redis:6379/0')
        self.assertFalse(values['PUBLIC_API_RESPONSE_CACHE_ENABLED'])
        with self.assertRaisesMessage(ImproperlyConfigured, 'caching to be disabled'):
            self.settings_block(PUBLIC_API_REDIS_MODE='shared', PUBLIC_API_RESPONSE_CACHE_ENABLED='true')
        with self.assertRaisesMessage(ImproperlyConfigured, 'dedicated or shared'):
            self.settings_block(PUBLIC_API_REDIS_MODE='typo')

    def test_redis_and_token_cannot_activate_without_attestation(self):
        environment = {
            "PUBLIC_API_RESOURCES_ENABLED": "true",
            "PUBLIC_API_REDIS_URL": "redis://public-redis:6379/0",
            "PUBLIC_API_METRICS_TOKEN": "a" * 32,
        }
        for attestation in ({}, {"PUBLIC_API_DEPLOYMENT_READY": "false"}):
            with (
                self.subTest(attestation=attestation),
                self.assertRaisesMessage(ImproperlyConfigured, "PUBLIC_API_DEPLOYMENT_READY"),
            ):
                self.settings_block(**environment, **attestation)
        values = self.settings_block(**environment, PUBLIC_API_DEPLOYMENT_READY="true")
        self.assertIs(values["PUBLIC_API_RESOURCES_ENABLED"], True)

    def test_attestation_does_not_replace_store_and_token_requirements(self):
        for extra in ({}, {"PUBLIC_API_REDIS_URL": "redis://public-redis:6379/0"}):
            with self.subTest(extra=extra), self.assertRaises(ImproperlyConfigured):
                self.settings_block(PUBLIC_API_RESOURCES_ENABLED="true", PUBLIC_API_DEPLOYMENT_READY="true", **extra)

    def test_lease_timing_is_bounded(self):
        for lease, heartbeat in (("0", "0.1"), ("301", "5"), ("30", "0"), ("30", "11"), ("30", "nan")):
            with self.subTest(lease=lease, heartbeat=heartbeat), self.assertRaises(ImproperlyConfigured):
                self.settings_block(PUBLIC_API_CACHE_LEASE_SECONDS=lease, PUBLIC_API_CACHE_HEARTBEAT_SECONDS=heartbeat)


class LeaseRedis:
    """Thread-safe expiring lease fake; real Redis below validates the Lua too."""

    def __init__(self):
        self.lock = Lock()
        self.owner = None
        self.expires = 0
        self.payload = ""
        self.renewed = Event()

    def eval(self, script, count, *args):
        with self.lock:
            if time.monotonic() >= self.expires:
                self.owner = None
            values = args[count:]
            if script == RESERVE:
                if self.payload:
                    return ["hit", self.payload]
                if self.owner:
                    return ["busy", ""]
                _, self.owner, lease, _ = values
                self.expires = time.monotonic() + lease
                return ["miss", ""]
            if self.owner != values[0]:
                return 0
            if script == RENEW:
                self.expires = time.monotonic() + values[1]
                self.renewed.set()
            elif script == FINISH:
                self.payload = values[1]
                self.owner = None
            else:
                raise AssertionError("Unexpected script")
            return 1


class SlowFillChecks:
    @override_settings(PUBLIC_API_RESPONSE_CACHE_ENABLED=True)
    def test_handler_failure_stops_heartbeat_and_releases_lease(self):
        heartbeats = []

        def heartbeat(*args):
            instance = FillHeartbeat(*args)
            heartbeats.append(instance)
            return instance

        @cached_public_get
        def handler(view, request):
            raise ValueError("handler failed")

        with (
            patch("bahk.public_api.v1.cache.canonical_parameters", return_value={}),
            patch("bahk.public_api.v1.cache.connection", return_value=self.redis),
            patch("bahk.public_api.v1.cache.FillHeartbeat", side_effect=heartbeat),
        ):
            for _ in range(2):
                with self.assertRaisesMessage(ValueError, "handler failed"):
                    handler(SimpleNamespace(), SimpleNamespace(_request=SimpleNamespace()))
        self.assertEqual(len(heartbeats), 2)
        self.assertTrue(all(not heartbeat.thread.is_alive() for heartbeat in heartbeats))

    @override_settings(
        PUBLIC_API_RESPONSE_CACHE_ENABLED=True,
        PUBLIC_API_CACHE_LEASE_SECONDS=1,
        PUBLIC_API_CACHE_HEARTBEAT_SECONDS=0.1,
    )
    def test_slow_handler_retains_lease_past_initial_expiry(self):
        entered, release = Event(), Event()
        calls = []
        heartbeats = []

        def heartbeat(*args):
            instance = FillHeartbeat(*args)
            heartbeats.append(instance)
            return instance

        @cached_public_get
        def handler(view, request):
            calls.append(1)
            entered.set()
            if not release.wait(5):
                raise AssertionError("Handler was not released")
            return Response({"ok": True})

        view = SimpleNamespace()

        def request():
            return SimpleNamespace(_request=SimpleNamespace())

        with (
            patch("bahk.public_api.v1.cache.canonical_parameters", return_value={}),
            patch("bahk.public_api.v1.cache.connection", return_value=self.redis),
            patch("bahk.public_api.v1.cache.FillHeartbeat", side_effect=heartbeat),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            first = pool.submit(handler, view, request())
            try:
                self.assertTrue(entered.wait(2))
                time.sleep(1.3)
                with self.assertRaises(PublicApiError) as error:
                    handler(view, request())
                self.assertEqual(error.exception.status_code, 503)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=2).status_code, 200)
            self.assertEqual(handler(view, request()).data, {"ok": True})
            self.assertEqual(len(calls), 1)
        self.assertFalse(heartbeats[0].thread.is_alive())

    def test_other_owner_cannot_renew_or_finish(self):
        keys = [self.prefix + ":index", self.prefix + ":data", self.prefix + ":lease"]
        self.assertEqual(self.redis.eval(RESERVE, 3, *keys, 10, "owner", 30, 300)[0], "miss")
        self.assertEqual(self.redis.eval(RENEW, 3, *keys, "intruder", 300), 0)
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, "intruder", "{}", 300), 0)
        self.assertEqual(self.redis.eval(RENEW, 3, *keys, "owner", 30), 1)
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, "owner", '{"owner":true}', 300), 1)

    def test_previous_owner_cannot_touch_replacement_lease(self):
        keys = [self.prefix + ":index", self.prefix + ":data", self.prefix + ":lease"]
        self.assertEqual(self.redis.eval(RESERVE, 3, *keys, 10, "old", 30, 300)[0], "miss")
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, "old", "", 300), 1)
        self.assertEqual(self.redis.eval(RESERVE, 3, *keys, 10, "new", 30, 300)[0], "miss")
        self.assertEqual(self.redis.eval(RENEW, 3, *keys, "old", 300), 0)
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, "old", "{}", 300), 0)
        self.assertEqual(self.redis.eval(RESERVE, 3, *keys, 10, "third", 30, 300)[0], "busy")
        self.assertEqual(self.redis.eval(FINISH, 3, *keys, "new", "", 300), 1)


class PublicFakeLeaseTests(SlowFillChecks, SimpleTestCase):
    def setUp(self):
        self.redis = LeaseRedis()
        self.prefix = "fake"

    def test_heartbeat_stops_on_ownership_loss_or_store_error(self):
        from redis.exceptions import ConnectionError

        for result in (0, ConnectionError("offline")):
            with self.subTest(result=result), override_settings(PUBLIC_API_CACHE_HEARTBEAT_SECONDS=0.1):
                heartbeat = FillHeartbeat(self.redis, ["index", "data", "lease"], "owner")
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with (
                    patch.object(self.redis, "eval", **kwargs),
                    patch("bahk.public_api.v1.cache.report_failure") as report,
                ):
                    heartbeat.thread.start()
                    try:
                        self.assertTrue(heartbeat.lost.wait(2))
                    finally:
                        heartbeat.stop()
                    report.assert_called_once_with("response_cache")
                    self.assertFalse(heartbeat.thread.is_alive())


@skipUnless(REDIS_URL, "Set PUBLIC_API_TEST_REDIS_URL for real-Redis integration checks")
class PublicRealLeaseTests(SlowFillChecks, RedisIsolation, SimpleTestCase):
    pass
