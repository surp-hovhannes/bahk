"""Tests for ConsecutiveFailureBreaker (hub.services.circuit_breaker).

Uses LocMemCache (tests.test_settings), which -- like Redis in production, unlike a
local loop variable -- persists across separate calls to the breaker within a test, so
these genuinely exercise the cross-call behavior the fix depends on.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from hub.services.circuit_breaker import ConsecutiveFailureBreaker


class ConsecutiveFailureBreakerTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_closed_by_default(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=3, cooldown_seconds=60)
        self.assertFalse(breaker.is_open())

    def test_opens_after_threshold_consecutive_failures(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=3, cooldown_seconds=60)

        breaker.record_failure()
        self.assertFalse(breaker.is_open())
        breaker.record_failure()
        self.assertFalse(breaker.is_open())
        breaker.record_failure()
        self.assertTrue(breaker.is_open())

    def test_success_resets_failure_streak(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=3, cooldown_seconds=60)

        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        breaker.record_failure()

        # Two failures since the reset -- one short of the threshold of three.
        self.assertFalse(breaker.is_open())

    def test_success_closes_an_open_breaker(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=1, cooldown_seconds=60)

        breaker.record_failure()
        self.assertTrue(breaker.is_open())

        breaker.record_success()
        self.assertFalse(breaker.is_open())

    def test_separate_names_do_not_interfere(self):
        """Distinct sources (e.g. NKJV vs KJVAIC) must not share state."""
        nkjv = ConsecutiveFailureBreaker("bible_api:NKJV", threshold=1, cooldown_seconds=60)
        kjvaic = ConsecutiveFailureBreaker("bible_api:KJVAIC", threshold=1, cooldown_seconds=60)

        nkjv.record_failure()

        self.assertTrue(nkjv.is_open())
        self.assertFalse(kjvaic.is_open())

    def test_open_expires_after_cooldown(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=1, cooldown_seconds=60)
        breaker.record_failure()
        self.assertTrue(breaker.is_open())

        # Simulate the cooldown elapsing rather than sleeping in the test.
        cache.delete(breaker._open_key())
        self.assertFalse(breaker.is_open())

    def test_is_open_fails_open_on_cache_error(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=1, cooldown_seconds=60)
        breaker.record_failure()
        self.assertTrue(breaker.is_open())

        with patch("hub.services.circuit_breaker.cache.get", side_effect=Exception("boom")):
            self.assertFalse(breaker.is_open())

    def test_record_failure_does_not_raise_on_cache_error(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=1, cooldown_seconds=60)
        with patch("hub.services.circuit_breaker.cache.incr", side_effect=Exception("boom")):
            breaker.record_failure()  # must not raise

    def test_record_success_does_not_raise_on_cache_error(self):
        breaker = ConsecutiveFailureBreaker("test", threshold=1, cooldown_seconds=60)
        with patch("hub.services.circuit_breaker.cache.delete", side_effect=Exception("boom")):
            breaker.record_success()  # must not raise
