"""Tests for feast view resilience: degraded responses, caching, circuit breaker."""
from datetime import date
from unittest.mock import Mock, patch
import urllib.error

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from hub.models import Church, Day, Feast, FeastContext
from hub.tasks.icon_tasks import match_icon_to_feast_task
from hub.utils import _fetch_sacredtradition, _stable_url_key
from hub.views.feasts import FeastContextFeedbackView, GetFeastForDate
from icons.models import Icon
from tests.fixtures.test_data import TestDataFactory


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
        feast1 = Feast.objects.create(day=day1, name="Christmas")
        other_date = date(2025, 1, 6)
        day2 = Day.objects.create(date=other_date, church=self.church)
        feast2 = Feast.objects.create(day=day2, name="Epiphany")
        other_church = Church.objects.create(name="Other Test Church")
        other_day = Day.objects.create(date=self.test_date, church=other_church)
        other_feast = Feast.objects.create(day=other_day, name="Other Christmas")
        user = TestDataFactory.create_user(username="cache-user")
        other_user = TestDataFactory.create_user(username="other-cache-user")
        TestDataFactory.create_profile(user=user, church=self.church)
        TestDataFactory.create_profile(user=other_user, church=other_church)

        feasts_by_date_and_church = {
            (self.test_date, self.church.id): feast1,
            (other_date, self.church.id): feast2,
            (self.test_date, other_church.id): other_feast,
        }

        def get_feast_for_request(date_obj, church, check_fast=False):
            return (
                feasts_by_date_and_church[(date_obj, church.id)],
                False,
                {"status": "success"},
            )

        mock_get_or_create.side_effect = get_feast_for_request

        factory = APIRequestFactory()

        # Call for first date
        request1 = factory.get(f'/feasts/?date={self.date_str}')
        force_authenticate(request1, user=user)
        response1 = GetFeastForDate.as_view()(request1)
        self.assertEqual(mock_get_or_create.call_count, 1)

        # Call for a different date — should NOT use cache
        request2 = factory.get(f'/feasts/?date={other_date.strftime("%Y-%m-%d")}')
        force_authenticate(request2, user=user)
        response2 = GetFeastForDate.as_view()(request2)
        self.assertEqual(mock_get_or_create.call_count, 2)
        self.assertEqual(response2.data['feast']['name'], "Epiphany")

        # Call for same date but a different church — should NOT use cache
        request3 = factory.get(f'/feasts/?date={self.date_str}')
        force_authenticate(request3, user=other_user)
        response3 = GetFeastForDate.as_view()(request3)
        self.assertEqual(mock_get_or_create.call_count, 3)
        self.assertEqual(response3.data['feast']['name'], "Other Christmas")

        # First date's cached response should still be same
        request4 = factory.get(f'/feasts/?date={self.date_str}')
        force_authenticate(request4, user=user)
        response4 = GetFeastForDate.as_view()(request4)
        self.assertEqual(mock_get_or_create.call_count, 3)  # Still cached
        self.assertEqual(response4.data['feast']['name'], response1.data['feast']['name'])
        self.assertEqual(response4.data['feast']['name'], "Christmas")


class FeastAPIRouteTests(TestCase):
    """Tests for the mounted /api/feasts/ route."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)
        self.date_str = self.test_date.strftime("%Y-%m-%d")
        self.hub_url = reverse("feast-for-date")
        cache.clear()

    def _create_feast(self, **kwargs):
        day = kwargs.pop(
            "day",
            Day.objects.create(date=self.test_date, church=self.church),
        )
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            return Feast.objects.create(day=day, **kwargs)

    def _create_icon(self, title="Nativity Icon"):
        return Icon.objects.create(
            title=title,
            church=self.church,
            image=SimpleUploadedFile(
                "test-icon.jpg",
                b"fake image content",
                content_type="image/jpeg",
            ),
            cached_thumbnail_url="https://example.com/test-icon.jpg",
        )

    def _get_cached_feast_response(self, feast):
        with patch("hub.views.feasts.generate_feast_context_task.delay"), patch(
            "hub.views.feasts.get_or_create_feast_for_date",
            return_value=(feast, False, {"status": "success"}),
        ):
            return self.client.get("/api/feasts/", {"date": self.date_str})

    def test_hub_feasts_url_resolves_to_feast_for_date_view(self):
        match = resolve("/hub/feasts/")

        self.assertEqual(match.func.view_class, GetFeastForDate)
        self.assertEqual(match.url_name, "feast-for-date")

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    @patch("hub.signals.match_icon_to_feast_task.delay")
    @patch("hub.signals.determine_feast_designation_task.delay")
    def test_api_route_returns_serialized_feast_for_anonymous_request(
        self,
        mock_determine_designation,
        mock_match_icon,
        mock_get_or_create,
        mock_generate_context,
    ):
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            day=day,
            name="Christmas",
            designation=Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        mock_get_or_create.return_value = (feast, False, {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["date"], self.date_str)
        self.assertEqual(data["feast"]["id"], feast.id)
        self.assertEqual(data["feast"]["name"], "Christmas")
        self.assertEqual(
            data["feast"]["designation"],
            Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        self.assertIsNone(data["feast"]["icon"])
        self.assertIsNone(data["feast"]["prayer"])
        self.assertEqual(data["feast"]["text"], "")
        self.assertEqual(data["feast"]["short_text"], "")
        self.assertEqual(data["feast"]["context_thumbs_up"], 0)
        self.assertEqual(data["feast"]["context_thumbs_down"], 0)
        mock_get_or_create.assert_called_once_with(
            self.test_date,
            self.church,
            check_fast=False,
        )

    @patch("hub.views.feasts.get_or_create_feast_for_date")
    def test_api_route_returns_null_feast_for_date_without_feast(
        self,
        mock_get_or_create,
    ):
        Day.objects.create(date=self.test_date, church=self.church)
        mock_get_or_create.return_value = (None, False, {"status": "not_found"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "date": self.date_str,
                "feast": None,
            },
        )

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    @patch("hub.signals.match_icon_to_feast_task.delay")
    @patch("hub.signals.determine_feast_designation_task.delay")
    def test_hub_route_returns_serialized_feast_for_anonymous_request(
        self,
        mock_determine_designation,
        mock_match_icon,
        mock_get_or_create,
        mock_generate_context,
    ):
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            day=day,
            name="Christmas",
            designation=Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        mock_get_or_create.return_value = (feast, False, {"status": "success"})

        response = self.client.get(self.hub_url, {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["date"], self.date_str)
        self.assertEqual(data["feast"]["id"], feast.id)
        self.assertEqual(data["feast"]["name"], "Christmas")
        self.assertEqual(
            data["feast"]["designation"],
            Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        self.assertIsNone(data["feast"]["icon"])
        self.assertIsNone(data["feast"]["prayer"])
        self.assertEqual(data["feast"]["text"], "")
        self.assertEqual(data["feast"]["short_text"], "")
        self.assertEqual(data["feast"]["context_thumbs_up"], 0)
        self.assertEqual(data["feast"]["context_thumbs_down"], 0)
        mock_get_or_create.assert_called_once_with(
            self.test_date,
            self.church,
            check_fast=False,
        )
        mock_generate_context.assert_called_once_with(feast.id)

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    @patch("hub.signals.match_icon_to_feast_task.delay")
    @patch("hub.signals.determine_feast_designation_task.delay")
    def test_hub_route_defaults_to_today_when_date_is_missing(
        self,
        mock_determine_designation,
        mock_match_icon,
        mock_get_or_create,
        mock_generate_context,
    ):
        today = date.today()
        Day.objects.create(date=today, church=self.church)
        mock_get_or_create.return_value = (None, False, {"status": "not_found"})

        response = self.client.get(self.hub_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"date": today.isoformat(), "feast": None})
        mock_get_or_create.assert_called_once_with(
            today,
            self.church,
            check_fast=False,
        )
        mock_generate_context.assert_not_called()

    def test_hub_route_rejects_invalid_date_format(self):
        response = self.client.get(self.hub_url, {"date": "12-25-2025"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Invalid date format. Expected format: YYYY-MM-DD",
            str(response.json()),
        )

    def test_feast_save_invalidates_cached_icon_response(self):
        feast = self._create_feast(name="Christmas")
        icon = self._create_icon()

        first_response = self._get_cached_feast_response(feast)
        self.assertIsNone(first_response.json()["feast"]["icon"])

        feast.icon = icon
        feast.save(update_fields=["icon"])

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.json()["feast"]["icon"]["id"], icon.id)

    def test_icon_matching_task_save_invalidates_cached_response(self):
        feast = self._create_feast(name="Nativity of Christ")
        icon = self._create_icon()

        first_response = self._get_cached_feast_response(feast)
        self.assertIsNone(first_response.json()["feast"]["icon"])

        with patch("hub.tasks.icon_tasks._match_icons_with_llm") as mock_match:
            mock_match.return_value = [{"id": icon.id, "confidence": "high"}]
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.json()["feast"]["icon"]["id"], icon.id)

    def test_feast_context_save_invalidates_cached_context_response(self):
        feast = self._create_feast(name="Christmas")
        context = FeastContext.objects.create(
            feast=feast,
            text="Old context",
            short_text="Old short",
        )

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(first_response.json()["feast"]["text"], "Old context")

        context.text = "New context"
        context.short_text = "New short"
        context.save(update_fields=["text", "short_text"])

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.json()["feast"]["text"], "New context")
        self.assertEqual(second_response.json()["feast"]["short_text"], "New short")

    def test_feast_context_feedback_invalidates_cached_vote_counts(self):
        feast = self._create_feast(name="Christmas")
        FeastContext.objects.create(
            feast=feast,
            text="Existing feast context",
            short_text="Existing short context",
        )

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(first_response.json()["feast"]["context_thumbs_up"], 0)

        feedback_response = self.client.post(
            reverse("feast-context-feedback", args=[feast.id]),
            data={"feedback_type": "up"},
            content_type="application/json",
        )

        self.assertEqual(feedback_response.status_code, status.HTTP_200_OK)
        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.json()["feast"]["context_thumbs_up"], 1)

    def test_icon_save_invalidates_cached_icon_payload(self):
        icon = self._create_icon(title="Old Icon Title")
        feast = self._create_feast(name="Christmas", icon=icon)

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            first_response.json()["feast"]["icon"]["title"],
            "Old Icon Title",
        )

        icon.title = "New Icon Title"
        icon.save(update_fields=["title"])

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            second_response.json()["feast"]["icon"]["title"],
            "New Icon Title",
        )

    def test_icon_tag_change_invalidates_cached_icon_payload(self):
        icon = self._create_icon()
        icon.tags.add("old-tag")
        feast = self._create_feast(name="Christmas", icon=icon)

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            first_response.json()["feast"]["icon"]["tag_list"],
            ["old-tag"],
        )

        icon.tags.add("new-tag")

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            set(second_response.json()["feast"]["icon"]["tag_list"]),
            {"old-tag", "new-tag"},
        )

    def test_feast_delete_invalidates_cached_response(self):
        feast = self._create_feast(name="Christmas")
        with patch("hub.views.feasts.generate_feast_context_task.delay"), patch(
            "hub.views.feasts.get_or_create_feast_for_date"
        ) as mock_get_or_create:
            mock_get_or_create.return_value = (feast, False, {"status": "success"})
            first_response = self.client.get("/api/feasts/", {"date": self.date_str})
            self.assertEqual(first_response.json()["feast"]["id"], feast.id)

            feast.delete()

            mock_get_or_create.return_value = (None, False, {"status": "not_found"})
            second_response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertIsNone(second_response.json()["feast"])


class FeastContextFeedbackAPITests(TestCase):
    """Tests for the mounted /api/feasts/<pk>/feedback/ route."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 12, 25), church=self.church)
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(day=self.day, name="Christmas")
        self.context = FeastContext.objects.create(
            feast=self.feast,
            text="Existing feast context",
            short_text="Existing short context",
        )
        self.url = reverse("feast-context-feedback", args=[self.feast.id])

    def test_mounted_url_resolves_to_feast_feedback_view(self):
        match = resolve(f"/hub/feasts/{self.feast.id}/feedback/")

        self.assertEqual(match.func.view_class, FeastContextFeedbackView)
        self.assertEqual(match.url_name, "feast-context-feedback")

    def test_feedback_up_accepts_anonymous_request_and_increments_context(self):
        response = self.client.post(
            self.url,
            data={"feedback_type": "up"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "success", "regenerate": False})
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_up, 1)
        self.assertEqual(self.context.thumbs_down, 0)

    def test_feedback_down_returns_regeneration_flag_at_threshold(self):
        with self.settings(FEAST_CONTEXT_REGENERATION_THRESHOLD=1), patch(
            "hub.views.feasts.generate_feast_context_task.delay"
        ) as mock_delay:
            response = self.client.post(
                self.url,
                data={"feedback_type": "down"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "success", "regenerate": True})
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_down, 1)
        mock_delay.assert_called_once_with(self.feast.id, force_regeneration=True)

    def test_feedback_rejects_missing_or_invalid_payload(self):
        missing_response = self.client.post(
            self.url,
            data={},
            content_type="application/json",
        )
        invalid_response = self.client.post(
            self.url,
            data={"feedback_type": "sideways"},
            content_type="application/json",
        )

        self.assertEqual(missing_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            invalid_response.json(),
            {"status": "error", "message": "Invalid feedback type"},
        )
        self.context.refresh_from_db()
        self.assertEqual(self.context.thumbs_up, 0)
        self.assertEqual(self.context.thumbs_down, 0)

    def test_feedback_returns_not_found_for_unknown_feast(self):
        url = reverse("feast-context-feedback", args=[self.feast.id + 999])

        response = self.client.post(
            url,
            data={"feedback_type": "up"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


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
