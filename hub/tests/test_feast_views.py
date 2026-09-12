"""Tests for feast view resilience: degraded responses, caching."""
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from hub.models import Church, Day, Feast, FeastContext
from hub.tasks.icon_tasks import match_icon_to_feast_task
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
        self.assertEqual(response.data['feasts'], [])
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
        self.assertEqual(response.data['feasts'], [])
        self.assertIn('error', response.data)

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_unavailable_feast_data_is_flagged_and_not_cached(self, mock_get_or_create):
        """A broken install must not look like a day that commemorates nobody.

        Both answers carry ``feasts: []`` -- there is nothing to render either way -- so the
        ``error`` key is the only thing separating them, and the response must stay out of the
        cache or one outage would be served for an hour after it ended.
        """
        from hub.services.feast_service import FeastDataUnavailable

        mock_get_or_create.side_effect = FeastDataUnavailable("observance catalog missing")

        response = self.client.get('/api/feasts/', {'date': self.date_str})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['feasts'], [])
        self.assertEqual(response.json()['error'], 'Feast data temporarily unavailable')

        # Nothing was cached: the second call re-asks rather than replaying the outage.
        self.client.get('/api/feasts/', {'date': self.date_str})
        self.assertEqual(mock_get_or_create.call_count, 2)


class FeastResponseShapeTransitionTests(TestCase):
    """The deprecated ``feast`` key served beside ``feasts`` while old app builds catch up."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.date_str = "2025-12-25"
        cache.clear()

    def _feast(self, name):
        return Feast.objects.create(church=self.church, name=name)

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_the_first_commemoration_is_mirrored_under_the_old_key(self, mock_get_or_create):
        """A build that predates the array reads ``feast`` and still shows a card.

        Without this it reads ``undefined`` and renders "no feast today" every day until its
        owner updates -- a mobile release is not an atomic deploy.
        """
        feasts = [self._feast("The Hermit St. Anton"), self._feast("The Hermit Sts. Tryphon")]
        mock_get_or_create.return_value = (feasts, {"status": "success"})

        data = self.client.get('/api/feasts/', {'date': self.date_str}).json()

        self.assertEqual([entry["name"] for entry in data["feasts"]],
                         ["The Hermit St. Anton", "The Hermit Sts. Tryphon"])
        self.assertEqual(data["feast"], data["feasts"][0])

    @patch('hub.views.feasts.get_or_create_feast_for_date')
    def test_a_day_with_no_commemoration_is_null_under_the_old_key(self, mock_get_or_create):
        mock_get_or_create.return_value = ([], {"status": "skipped"})

        data = self.client.get('/api/feasts/', {'date': self.date_str}).json()

        self.assertEqual(data["feasts"], [])
        self.assertIsNone(data["feast"])

    def test_the_cache_key_carries_the_response_shape(self):
        """A revert has to stop reading entries the newer shape wrote.

        Bumping the per-church generation on deploy would orphan them going forward but not
        backward: the older code returns a cached body without inspecting it, so it would serve
        the new shape to the very clients the rollback was meant to rescue.
        """
        from hub.cache import FEAST_API_RESPONSE_SHAPE, feast_api_cache_key

        self.assertIn(f"s{FEAST_API_RESPONSE_SHAPE}",
                      feast_api_cache_key(date(2025, 12, 25), self.church.id, "en"))


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
        feast = Feast.objects.create(church=day.church, name="Christmas")
        mock_get_or_create.return_value = ([feast], {"status": "success"})

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
        feast1 = Feast.objects.create(church=day1.church, name="Christmas")
        other_date = date(2025, 1, 6)
        day2 = Day.objects.create(date=other_date, church=self.church)
        feast2 = Feast.objects.create(church=day2.church, name="Epiphany")
        other_church = Church.objects.create(name="Other Test Church")
        other_day = Day.objects.create(date=self.test_date, church=other_church)
        other_feast = Feast.objects.create(church=other_day.church, name="Other Christmas")
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
                [feasts_by_date_and_church[(date_obj, church.id)]],
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
        self.assertEqual(response2.data['feasts'][0]['name'], "Epiphany")

        # Call for same date but a different church — should NOT use cache
        request3 = factory.get(f'/feasts/?date={self.date_str}')
        force_authenticate(request3, user=other_user)
        response3 = GetFeastForDate.as_view()(request3)
        self.assertEqual(mock_get_or_create.call_count, 3)
        self.assertEqual(response3.data['feasts'][0]['name'], "Other Christmas")

        # First date's cached response should still be same
        request4 = factory.get(f'/feasts/?date={self.date_str}')
        force_authenticate(request4, user=user)
        response4 = GetFeastForDate.as_view()(request4)
        self.assertEqual(mock_get_or_create.call_count, 3)  # Still cached
        self.assertEqual(response4.data['feasts'][0]['name'], response1.data['feasts'][0]['name'])
        self.assertEqual(response4.data['feasts'][0]['name'], "Christmas")


class FeastAPIRouteTests(TestCase):
    """Tests for the mounted /api/feasts/ route."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)
        self.date_str = self.test_date.strftime("%Y-%m-%d")
        self.hub_url = reverse("feast-for-date")
        cache.clear()

    def _create_feast(self, **kwargs):
        day = kwargs.pop("day", None)
        if day is None:
            day = Day.objects.create(date=self.test_date, church=self.church)
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            return Feast.objects.create(church=day.church, **kwargs)

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
            return_value=([feast], {"status": "success"}),
        ):
            return self.client.get("/api/feasts/", {"date": self.date_str})

    def test_hub_feasts_url_resolves_to_feast_for_date_view(self):
        match = resolve("/hub/feasts/")

        self.assertEqual(match.func.view_class, GetFeastForDate)
        self.assertEqual(match.url_name, "feast-for-date")

    def test_get_queryset_is_configured_for_drf_introspection(self):
        queryset = GetFeastForDate().get_queryset()

        self.assertEqual(queryset.model, Feast)

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
            church=day.church,
            name="Christmas",
            designation=Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["date"], self.date_str)
        self.assertEqual(data["feasts"][0]["id"], feast.id)
        self.assertEqual(data["feasts"][0]["name"], "Christmas")
        self.assertEqual(
            data["feasts"][0]["designation"],
            Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        self.assertIsNone(data["feasts"][0]["icon"])
        self.assertIsNone(data["feasts"][0]["prayer"])
        self.assertEqual(data["feasts"][0]["text"], "")
        self.assertEqual(data["feasts"][0]["short_text"], "")
        self.assertEqual(data["feasts"][0]["context_thumbs_up"], 0)
        self.assertEqual(data["feasts"][0]["context_thumbs_down"], 0)
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
        mock_get_or_create.return_value = ([], {"status": "not_found"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "date": self.date_str,
                "feasts": [],
                # Deprecated mirror for pre-array app builds; see FeastResponseShapeTransitionTests.
                "feast": None,
            },
        )

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    def test_api_route_enqueues_context_for_fast_named_real_commemoration(
        self,
        mock_get_or_create,
        mock_generate_context,
    ):
        fast = TestDataFactory.create_fast(
            church=self.church,
            name="Fast of our Holy Father St Gregory the Illuminator",
        )
        day = Day.objects.create(date=self.test_date, church=self.church, fast=fast)
        feast = self._create_feast(
            name=(
                "Fast day, Saints Epiphanius Bishop of Cyprus, Babylas the "
                "Patriarch, and his three disciples"
            ),
            designation=Feast.Designation.MARTYRS,
        )
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["feasts"][0]["designation"], Feast.Designation.MARTYRS)
        self.assertEqual(data["feasts"][0]["text"], "")
        self.assertEqual(data["feasts"][0]["short_text"], "")
        mock_generate_context.assert_called_once_with(feast.id)

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    def test_api_route_enqueues_context_for_unclassified_fast_named_commemoration(
        self,
        mock_get_or_create,
        mock_generate_context,
    ):
        feast = self._create_feast(
            name=(
                "Fast day, Saints Epiphanius Bishop of Cyprus, Babylas the "
                "Patriarch, and his three disciples"
            ),
        )
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsNone(data["feasts"][0]["designation"])
        self.assertEqual(data["feasts"][0]["text"], "")
        self.assertEqual(data["feasts"][0]["short_text"], "")
        mock_generate_context.assert_called_once_with(feast.id)

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    def test_api_route_does_not_enqueue_context_for_generic_fast_designation(
        self,
        mock_get_or_create,
        mock_generate_context,
    ):
        feast = self._create_feast(
            name="First day of the Fast",
            designation=Feast.Designation.FAST,
        )
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["feasts"][0]["designation"], Feast.Designation.FAST)
        self.assertEqual(data["feasts"][0]["text"], "")
        self.assertEqual(data["feasts"][0]["short_text"], "")
        mock_generate_context.assert_not_called()

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    def test_eligibility_no_longer_guesses_from_a_fast_shaped_name(
        self,
        mock_get_or_create,
        mock_generate_context,
    ):
        """A fast-shaped name on an unclassified row is no longer a reason to withhold context.

        It used to be: a regex looked for "fast"/"lent" plus "day" and blocked generation. That
        matched display text the engine is free to rewrite, which is the failure the observance
        id layer exists to prevent -- and it is redundant now, because a row exists only for an
        observance the engine marks as a commemoration. Whether a day is a fast is answered by
        the designation, not by how its name reads.
        """
        feast = self._create_feast(name="First day of the Fast")
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsNone(data["feasts"][0]["designation"])
        mock_generate_context.assert_called_once_with(feast.id)

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    @patch("hub.signals.match_icon_to_feast_task.delay")
    @patch("hub.signals.determine_feast_designation_task.delay")
    def test_api_route_enqueues_context_for_mijink(
        self,
        mock_determine_designation,
        mock_match_icon,
        mock_get_or_create,
        mock_generate_context,
    ):
        """Mijink gets context now, and the old code contradicted itself about it.

        ``_GENERIC_FAST_DAY_TOKENS`` listed Mijink and blocked generation, while
        ``determine_feast_designation_task`` exempted it by name as "a named feast, not a generic
        fast day." The engine settles it: ``twenty_fourth_day_of_great_lent`` is marked both a
        fast and a commemoration, and the marks are independent -- so it is a commemoration, and
        commemorations get context.
        """
        feast = self._create_feast(name="Median day of Great Lent (Mijink)")
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsNone(data["feasts"][0]["designation"])
        mock_generate_context.assert_called_once_with(feast.id)

    @patch("hub.views.feasts.generate_feast_context_task.delay")
    @patch("hub.views.feasts.get_or_create_feast_for_date")
    @patch("hub.signals.match_icon_to_feast_task.delay")
    @patch("hub.signals.determine_feast_designation_task.delay")
    def test_api_route_enqueues_context_for_unclassified_plural_saints(
        self,
        mock_determine_designation,
        mock_match_icon,
        mock_get_or_create,
        mock_generate_context,
    ):
        feast = self._create_feast(name="Commemoration of Sts. Martyrs")
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsNone(data["feasts"][0]["designation"])
        mock_generate_context.assert_called_once_with(feast.id)

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
            church=day.church,
            name="Christmas",
            designation=Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        mock_get_or_create.return_value = ([feast], {"status": "success"})

        response = self.client.get(self.hub_url, {"date": self.date_str})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["date"], self.date_str)
        self.assertEqual(data["feasts"][0]["id"], feast.id)
        self.assertEqual(data["feasts"][0]["name"], "Christmas")
        self.assertEqual(
            data["feasts"][0]["designation"],
            Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )
        self.assertIsNone(data["feasts"][0]["icon"])
        self.assertIsNone(data["feasts"][0]["prayer"])
        self.assertEqual(data["feasts"][0]["text"], "")
        self.assertEqual(data["feasts"][0]["short_text"], "")
        self.assertEqual(data["feasts"][0]["context_thumbs_up"], 0)
        self.assertEqual(data["feasts"][0]["context_thumbs_down"], 0)
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
        mock_get_or_create.return_value = ([], {"status": "not_found"})

        response = self.client.get(self.hub_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {"date": today.isoformat(), "feasts": [], "feast": None},
        )
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

    def test_hub_route_rejects_invalid_date_format_for_browsable_api(self):
        response = self.client.get(
            self.hub_url,
            {"date": "2026"},
            HTTP_ACCEPT="text/html",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            b"Invalid date format. Expected format: YYYY-MM-DD",
            response.content,
        )

    def test_feast_save_invalidates_cached_icon_response(self):
        feast = self._create_feast(name="Christmas")
        icon = self._create_icon()

        first_response = self._get_cached_feast_response(feast)
        self.assertIsNone(first_response.json()["feasts"][0]["icon"])

        feast.icon = icon
        with self.captureOnCommitCallbacks(execute=True):
            feast.save(update_fields=["icon"])

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.json()["feasts"][0]["icon"]["id"], icon.id)

    def test_icon_matching_task_save_invalidates_cached_response(self):
        feast = self._create_feast(name="Nativity of Christ")
        icon = self._create_icon()

        first_response = self._get_cached_feast_response(feast)
        self.assertIsNone(first_response.json()["feasts"][0]["icon"])

        with self.captureOnCommitCallbacks(execute=True):
            with patch("hub.tasks.icon_tasks.match_icons") as mock_match:
                from hub.services.icon_match_service import IconMatchOutcome
                mock_match.return_value = IconMatchOutcome(status="complete", matches=[
                    {"id": icon.id, "match_tier": "direct_exact", "confidence": "high", "auto_assignable": True}
                ])
                match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.json()["feasts"][0]["icon"]["id"], icon.id)

    def test_feast_context_save_invalidates_cached_context_response(self):
        feast = self._create_feast(name="Christmas")
        context = FeastContext.objects.create(
            feast=feast,
            text="Old context",
            short_text="Old short",
        )

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(first_response.json()["feasts"][0]["text"], "Old context")

        context.text = "New context"
        context.short_text = "New short"
        context.save(update_fields=["text", "short_text"])

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.json()["feasts"][0]["text"], "New context")
        self.assertEqual(second_response.json()["feasts"][0]["short_text"], "New short")

    def test_feast_context_feedback_invalidates_cached_vote_counts(self):
        feast = self._create_feast(name="Christmas")
        FeastContext.objects.create(
            feast=feast,
            text="Existing feast context",
            short_text="Existing short context",
        )

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(first_response.json()["feasts"][0]["context_thumbs_up"], 0)

        feedback_response = self.client.post(
            reverse("feast-context-feedback", args=[feast.id]),
            data={"feedback_type": "up"},
            content_type="application/json",
        )

        self.assertEqual(feedback_response.status_code, status.HTTP_200_OK)
        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(second_response.json()["feasts"][0]["context_thumbs_up"], 1)

    def test_icon_save_invalidates_cached_icon_payload(self):
        icon = self._create_icon(title="Old Icon Title")
        feast = self._create_feast(name="Christmas", icon=icon)

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            first_response.json()["feasts"][0]["icon"]["title"],
            "Old Icon Title",
        )

        icon.title = "New Icon Title"
        icon.save(update_fields=["title"])

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            second_response.json()["feasts"][0]["icon"]["title"],
            "New Icon Title",
        )

    def test_icon_tag_change_invalidates_cached_icon_payload(self):
        icon = self._create_icon()
        icon.tags.add("old-tag")
        feast = self._create_feast(name="Christmas", icon=icon)

        first_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            first_response.json()["feasts"][0]["icon"]["tag_list"],
            ["old-tag"],
        )

        icon.tags.add("new-tag")

        second_response = self._get_cached_feast_response(feast)
        self.assertEqual(
            set(second_response.json()["feasts"][0]["icon"]["tag_list"]),
            {"old-tag", "new-tag"},
        )

    def test_feast_delete_invalidates_cached_response(self):
        feast = self._create_feast(name="Christmas")
        with patch("hub.views.feasts.generate_feast_context_task.delay"), patch(
            "hub.views.feasts.get_or_create_feast_for_date"
        ) as mock_get_or_create:
            mock_get_or_create.return_value = ([feast], {"status": "success"})
            first_response = self.client.get("/api/feasts/", {"date": self.date_str})
            self.assertEqual(first_response.json()["feasts"][0]["id"], feast.id)

            feast.delete()

            mock_get_or_create.return_value = ([], {"status": "not_found"})
            second_response = self.client.get("/api/feasts/", {"date": self.date_str})

        self.assertEqual(second_response.json()["feasts"], [])


class FeastContextFeedbackAPITests(TestCase):
    """Tests for the mounted /api/feasts/<pk>/feedback/ route."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 12, 25), church=self.church)
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(church=self.day.church, name="Christmas")
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
