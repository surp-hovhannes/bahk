"""Tests for feast view resilience: degraded responses, caching, circuit breaker."""
from datetime import date
from unittest.mock import Mock, patch
import urllib.error

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from hub.models import Church, Day, Feast
from hub.utils import _fetch_sacredtradition, _stable_url_key


class FeastViewDegradedResponseTests(TestCase):
    """Tests for degraded feast endpoint responses on scrape failure."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)
        self.date_str = self.test_date.strftime("%Y-%m-%d")
        cache.clear()

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_degraded_response_on_scrape_failure(self, mock_get_or_create):
        """Endpoint returns 200 with feast:None when get_or_create_feast_for_date raises."""
        from hub.views.feasts import GetFeastForDate

        mock_get_or_create.side_effect = Exception("Scraper timeout")

        factory = APIRequestFactory()
        request = factory.get(f'/feasts/?date={self.date_str}')
        view = GetFeastForDate.as_view()

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['feast'])
        self.assertIn('error', response.data)
        self.assertEqual(response.data['error'], 'Feast data temporarily unavailable')

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_graceful_response_on_database_error(self, mock_get_or_create):
        """Endpoint returns degraded response on DB errors too."""
        from hub.views.feasts import GetFeastForDate

        # Simulate a DB error during the view logic
        mock_get_or_create.side_effect = RuntimeError("Database connection lost")

        factory = APIRequestFactory()
        request = factory.get(f'/feasts/?date={self.date_str}')
        view = GetFeastForDate.as_view()

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['feast'])
        self.assertIn('error', response.data)


class FeastViewCacheTests(TestCase):
    """Tests for feast endpoint caching."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)
        self.date_str = self.test_date.strftime("%Y-%m-%d")
        cache.clear()

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_cache_hit_prevents_scraper_call(self, mock_get_or_create):
        """Second call to endpoint uses cache and does not call scraper."""
        from hub.views.feasts import GetFeastForDate

        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(day=day, name="Christmas")
        mock_get_or_create.return_value = (feast, False, {"status": "success"})

        factory = APIRequestFactory()

        # First call — should call get_or_create_feast_for_date
        request1 = factory.get(f'/feasts/?date={self.date_str}')
        view1 = GetFeastForDate.as_view()
        response1 = view1(request1)
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(mock_get_or_create.call_count, 1)

        # Second call — should hit cache, NOT call scraper again
        request2 = factory.get(f'/feasts/?date={self.date_str}')
        view2 = GetFeastForDate.as_view()
        response2 = view2(request2)
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.data, response1.data)
        # Call count should still be 1 (cached, not re-scraped)
        self.assertEqual(mock_get_or_create.call_count, 1)

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_cache_key_isolation(self, mock_get_or_create):
        """Different dates and churches use different cache keys."""
        from hub.views.feasts import GetFeastForDate

        day1 = Day.objects.create(date=self.test_date, church=self.church)
        Feast.objects.create(day=day1, name="Christmas")
        mock_get_or_create.return_value = (day1.feasts.first(), False, {"status": "success"})

        factory = APIRequestFactory()

        # Call for first date
        request1 = factory.get(f'/feasts/?date={self.date_str}')
        response1 = GetFeastForDate.as_view()(request1)
        self.assertEqual(mock_get_or_create.call_count, 1)

        # Call for a different date — should NOT use cache
        other_date = date(2025, 1, 6)
        request2 = factory.get(f'/feasts/?date={other_date.strftime("%Y-%m-%d")}')
        response2 = GetFeastForDate.as_view()(request2)
        self.assertEqual(mock_get_or_create.call_count, 2)

        # First date's cached response should still be same
        request3 = factory.get(f'/feasts/?date={self.date_str}')
        response3 = GetFeastForDate.as_view()(request3)
        self.assertEqual(mock_get_or_create.call_count, 2)  # Still cached
        self.assertEqual(response3.data['feast']['name'], response1.data['feast']['name'])


class CircuitBreakerTests(TestCase):
    """Tests for the circuit breaker in _fetch_sacredtradition."""

    def setUp(self):
        cache.clear()

    def test_url_validation_invalid(self):
        """_fetch_sacredtradition returns None for invalid URLs."""
        # Wrong domain
        result = _fetch_sacredtradition("https://evil.example.com/page")
        self.assertIsNone(result)

        # No netloc
        result = _fetch_sacredtradition("not-a-url")
        self.assertIsNone(result)

    @patch('urllib.request.urlopen')
    def test_circuit_breaker_trips_after_failures(self, mock_urlopen):
        """After 3+ failures, circuit breaker opens and subsequent calls return None."""
        url = "https://sacredtradition.am/Calendar/nter.php?NM=0&iM=1103&iL=2&ymd=20251225"

        # Simulate 3 consecutive failures
        mock_urlopen.side_effect = urllib.error.URLError("Connection timeout")

        # First 3 calls should fail
        for _ in range(3):
            result = _fetch_sacredtradition(url)
            self.assertIsNone(result)

        # Circuit breaker should now be open
        circuit_key = f"circuit_breaker:{_stable_url_key(url)}"
        self.assertTrue(cache.get(circuit_key))

        # Fourth call should return None immediately (circuit open)
        mock_urlopen.reset_mock()
        result = _fetch_sacredtradition(url)
        self.assertIsNone(result)
        # urlopen should NOT have been called (circuit breaker blocked it)
        mock_urlopen.assert_not_called()

    @patch('urllib.request.urlopen')
    def test_circuit_breaker_resets_on_success(self, mock_urlopen):
        """A successful call resets the circuit breaker."""
        url = "https://sacredtradition.am/Calendar/nter.php?NM=0&iM=1103&iL=2&ymd=20251225"

        # Use a side_effect function to simulate failures then success
        call_count = [0]
        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 6:  # 2 calls × 3 retries each
                raise urllib.error.URLError("Connection timeout")
            mock_resp = Mock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'<html><div class="dname">Test</div></html>'
            return mock_resp

        mock_urlopen.side_effect = _side_effect

        _fetch_sacredtradition(url)
        _fetch_sacredtradition(url)

        circuit_key = f"circuit_breaker:{_stable_url_key(url)}"
        # After 2 failures (< max), circuit should NOT be open
        self.assertIsNone(cache.get(circuit_key))

        # Now the third call should succeed (side_effect function returns success for call_count > 6)
        mock_html = b'<html><div class="dname">Test</div></html>'
        mock_response = Mock()
        mock_response.status = 200
        mock_response.read.return_value = mock_html
        mock_urlopen.side_effect = [mock_response]

        result = _fetch_sacredtradition(url)
        self.assertIsNotNone(result)

        circuit_key = f"circuit_breaker:{_stable_url_key(url)}"
        # Circuit breaker should be cleared
        self.assertIsNone(cache.get(circuit_key))

    @patch('urllib.request.urlopen')
    def test_cache_returns_stale_on_failure(self, mock_urlopen):
        """When scrape fails, cached stale data is returned as fallback."""
        url = "https://sacredtradition.am/Calendar/nter.php?NM=0&iM=1103&iL=2&ymd=20251225"

        # First, a successful fetch that caches the result
        mock_html = b'<html><div class="dname">Cached Feast</div></html>'
        mock_response = Mock()
        mock_response.status = 200
        mock_response.read.return_value = mock_html
        mock_urlopen.return_value = mock_response

        result1 = _fetch_sacredtradition(url)
        self.assertIsNotNone(result1)
        self.assertIn("Cached Feast", result1)

        # Now simulate 3 consecutive failures
        mock_urlopen.side_effect = urllib.error.URLError("Connection timeout")

        for _ in range(3):
            result = _fetch_sacredtradition(url)
            # Should return cached data even on failure
            self.assertIsNotNone(result)
            self.assertIn("Cached Feast", result)


    @patch('urllib.request.urlopen')
    def test_failure_counter_resets_on_success(self, mock_urlopen):
        """Failure counter is cleared when a fetch succeeds (before circuit trips)."""
        url = "https://sacredtradition.am/Calendar/nter.php?NM=0&iM=1103&iL=2&ymd=20251225"
        circuit_key = f"circuit_breaker:{_stable_url_key(url)}"
        circuit_failures_key = circuit_key + ":failures"

        mock_html = b'<html><div class="dname">Test Feast</div></html>'
        mock_success = Mock()
        mock_success.status = 200
        mock_success.read.return_value = mock_html

        # Set up: cache some data for stale fallback, prime counter at 2
        cache.set(f"scrape_result:{_stable_url_key(url)}", '<html><div class="dname">Test Feast</div></html>', 21600)
        cache.set(circuit_failures_key, 2, 900)  # 2 prior failures

        # Success resets the counter (even though circuit isn't open yet)
        mock_urlopen.return_value = mock_success
        result = _fetch_sacredtradition(url)
        self.assertIsNotNone(result)
        # Counter should be reset by success
        self.assertIsNone(cache.get(circuit_failures_key))
        self.assertIsNone(cache.get(circuit_key))

        # Now 2 failures: should land at count 2, not 4 (reset works)
        mock_urlopen.side_effect = urllib.error.URLError("Connection timeout")
        for _ in range(2):
            _fetch_sacredtradition(url)
        self.assertEqual(cache.get(circuit_failures_key), 2)
        self.assertIsNone(cache.get(circuit_key))  # not tripped yet
