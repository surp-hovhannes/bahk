"""Tests for the staff-only feast icon assignment API."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from hub.cache import feast_api_cache_key
from hub.models import Church, Day, Feast
from hub.tasks.icon_tasks import match_icon_to_feast_task
from icons.models import Icon


class FeastAssignIconAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff-assign",
            email="staff-assign@example.com",
            password="password",
            is_staff=True,
        )
        self.regular_user = user_model.objects.create_user(
            username="regular-assign",
            email="regular-assign@example.com",
            password="password",
        )
        self.client = APIClient()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.other_church = Church.objects.create(name="Other Church")
        self.day = Day.objects.create(date=date(2026, 7, 23), church=self.church)
        self.icon = self._create_icon("Requested Icon", self.church)
        self.other_icon = self._create_icon("Other Icon", self.church)
        self.cross_church_icon = self._create_icon("Cross Church Icon", self.other_church)
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(church=self.day.church, name="Test Feast")
        self.url = reverse("feast-assign-icon", kwargs={"feast_id": self.feast.pk})

    @staticmethod
    def _create_icon(title, church):
        return Icon.objects.create(
            title=title,
            church=church,
            image=SimpleUploadedFile(
                name=f"{title.lower().replace(' ', '-')}.jpg",
                content=b"fake image content",
                content_type="image/jpeg",
            ),
        )

    def _authenticate_staff(self):
        self.client.force_authenticate(user=self.staff_user)

    def _post(self, **overrides):
        payload = {
            "icon_id": self.icon.id,
            "replace": False,
            "expected_current_icon_id": None,
            **overrides,
        }
        return self.client.post(self.url, payload, format="json")

    def test_staff_preflight_returns_exact_iconless_and_assigned_snapshots(self):
        self._authenticate_staff()
        response = self.client.get(self.url)
        self.assertEqual(
            response.json(),
            {
                "feast_id": self.feast.id,
                # No "date": a feast is a commemoration, served on every day the engine names it.
                "name": self.feast.name,
                "church_id": self.church.id,
                "current_icon_id": None,
                "current_icon": None,
            },
        )

        self.feast.icon = self.other_icon
        self.feast.save(update_fields=["icon"])
        response = self.client.get(self.url)
        self.assertEqual(response.json()["current_icon_id"], self.other_icon.id)
        self.assertEqual(
            response.json()["current_icon"],
            {"id": self.other_icon.id, "title": "Other Icon"},
        )

    def test_get_and_post_require_staff(self):
        for method in (self.client.get, lambda url: self.client.post(url, {}, format="json")):
            response = method(self.url)
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.regular_user)
        for method in (self.client.get, lambda url: self.client.post(url, {}, format="json")):
            response = method(self.url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_feast_and_icon_return_404(self):
        self._authenticate_staff()
        missing_url = reverse("feast-assign-icon", kwargs={"feast_id": 999999})
        self.assertEqual(self.client.get(missing_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self._post(icon_id=999999).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_cross_church_icon_is_rejected_without_mutation(self):
        self._authenticate_staff()
        response = self._post(icon_id=self.cross_church_icon.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("church", response.json()["icon_id"])
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)

    def test_payload_types_are_validated_deliberately(self):
        self._authenticate_staff()
        response = self.client.post(self.url, [self.icon.id], format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non_field_errors", response.json())

        cases = [
            ({"icon_id": True}, "icon_id"),
            ({"replace": 1}, "replace"),
            ({"expected_current_icon_id": 0}, "expected_current_icon_id"),
        ]
        for overrides, field in cases:
            response = self._post(**overrides)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, response.json())

        response = self.client.post(
            self.url,
            {"icon_id": self.icon.id, "replace": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expected_current_icon_id", response.json())

    def test_first_assignment_requires_null_expected_id_and_invalidates_cache(self):
        self._authenticate_staff()
        cache_key = feast_api_cache_key(self.day.date, self.church.id, "en")
        cache.set(cache_key, {"stale": True}, 60)

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], "assigned")
        self.assertIsNone(response.json()["previous_icon_id"])
        self.assertEqual(response.json()["current_icon_id"], self.icon.id)
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.icon.id)
        # Invalidation bumps the church's cache generation rather than deleting keys, so the
        # entry is orphaned rather than removed: the key the view builds now is a different one,
        # and it is empty.
        self.assertNotEqual(
            feast_api_cache_key(self.day.date, self.church.id, "en"), cache_key)
        self.assertIsNone(
            cache.get(feast_api_cache_key(self.day.date, self.church.id, "en")))

    def test_existing_icon_requires_explicit_replacement(self):
        self._authenticate_staff()
        self.feast.icon = self.other_icon
        self.feast.save(update_fields=["icon"])

        response = self._post(expected_current_icon_id=self.other_icon.id)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "replacement_required")
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.other_icon.id)

    def test_explicit_replacement_succeeds(self):
        self._authenticate_staff()
        self.feast.icon = self.other_icon
        self.feast.save(update_fields=["icon"])

        response = self._post(
            replace=True,
            expected_current_icon_id=self.other_icon.id,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], "replaced")
        self.assertEqual(response.json()["previous_icon_id"], self.other_icon.id)
        self.assertEqual(response.json()["current_icon_id"], self.icon.id)
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.icon.id)

    def test_same_icon_is_noop_without_save(self):
        self._authenticate_staff()
        self.feast.icon = self.icon
        self.feast.save(update_fields=["icon"])

        with patch.object(Feast, "save", autospec=True) as mock_save:
            response = self._post(expected_current_icon_id=self.icon.id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], "unchanged")
        mock_save.assert_not_called()

    def test_later_assignment_makes_preflight_stale_and_wins_over_replacement_policy(self):
        self._authenticate_staff()
        preflight = self.client.get(self.url).json()
        self.assertIsNone(preflight["current_icon_id"])
        self.feast.icon = self.other_icon
        self.feast.save(update_fields=["icon"])

        response = self._post(replace=True)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "stale_assignment")
        self.assertEqual(response.json()["current_icon_id"], self.other_icon.id)
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.other_icon.id)

    def test_matcher_rechecks_locked_feast_after_manual_assignment_wins(self):
        self._authenticate_staff()

        with patch("hub.tasks.icon_tasks._match_icons_with_llm") as mock_match:
            def assign_manually_before_matcher_save(*args, **kwargs):
                self.assertEqual(self._post().status_code, status.HTTP_200_OK)
                return [
                    {
                        "id": self.other_icon.id,
                        "match_tier": "direct_exact",
                        "confidence": "high",
                    }
                ]

            mock_match.side_effect = assign_manually_before_matcher_save
            match_icon_to_feast_task(self.feast.id)

        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.icon.id)

    def test_rollback_preserves_assignment_and_cache(self):
        self._authenticate_staff()
        cache_key = feast_api_cache_key(self.day.date, self.church.id, "en")
        cache.set(cache_key, {"still": "valid"}, 60)
        original_save = Feast.save

        def save_then_fail(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            raise RuntimeError("force transaction rollback")

        self.client.raise_request_exception = False
        with patch.object(Feast, "save", new=save_then_fail):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self._post()

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(callbacks, [])
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)
        self.assertEqual(cache.get(cache_key), {"still": "valid"})
