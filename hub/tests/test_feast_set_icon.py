"""Tests for exact, operator-approved feast icon assignment."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from hub.cache import feast_api_cache_key
from hub.models import Church, Day, Feast
from hub.views.feasts import FeastSetIconView
from icons.models import Icon


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class FeastSetIconTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_user(
            username="set-icon-admin",
            password="password",
            is_staff=True,
        )
        self.regular_user = user_model.objects.create_user(
            username="set-icon-user",
            password="password",
        )
        self.church = Church.objects.create(name="Set Icon Church")
        self.other_church = Church.objects.create(name="Other Set Icon Church")
        self.day = Day.objects.create(date=date(2026, 7, 23), church=self.church)
        self.icon = self._create_icon("Requested Icon", self.church)
        self.replacement = self._create_icon("Replacement Icon", self.church)
        self.cross_church_icon = self._create_icon("Cross Church Icon", self.other_church)

        self.match_patcher = patch("hub.signals.match_icon_to_feast_task.delay")
        self.designation_patcher = patch(
            "hub.signals.determine_feast_designation_task.delay"
        )
        self.mock_match = self.match_patcher.start()
        self.mock_designation = self.designation_patcher.start()
        self.addCleanup(self.match_patcher.stop)
        self.addCleanup(self.designation_patcher.stop)
        self.feast = Feast.objects.create(church=self.church, name="Set Icon Feast")
        self.mock_match.reset_mock()
        self.mock_designation.reset_mock()

        self.factory = APIRequestFactory()
        self.url = reverse("feast-set-icon", kwargs={"feast_id": self.feast.pk})
        self.view = FeastSetIconView.as_view()

    @staticmethod
    def _create_icon(title, church):
        return Icon.objects.create(
            title=title,
            church=church,
            image=SimpleUploadedFile(
                f"{title.lower().replace(' ', '-')}.jpg",
                b"image",
                content_type="image/jpeg",
            ),
        )

    def _post(self, payload, user=None):
        request = self.factory.post(self.url, payload, format="json")
        if user is not None:
            force_authenticate(request, user=user)
        return self.view(request, feast_id=self.feast.pk)

    def test_anonymous_request_is_rejected(self):
        response = self._post({"icon_id": self.icon.pk})

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_non_admin_is_rejected(self):
        response = self._post({"icon_id": self.icon.pk}, self.regular_user)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_assigns_icon_and_returns_result_shape(self):
        with patch("hub.views.feasts.invalidate_feast_api_cache_for_feast") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._post({"icon_id": self.icon.pk}, self.admin_user)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "status": "ASSIGNED",
                "feast_id": self.feast.pk,
                "feast_name": self.feast.name,
                "date": None,
                "church_id": self.church.pk,
                "church_name": self.church.name,
                "current_icon_id": None,
                "current_icon_title": None,
                "requested_icon_id": self.icon.pk,
                "requested_icon_title": self.icon.title,
            },
        )
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.icon.pk)
        invalidate.assert_called_once()
        self.assertEqual(invalidate.call_args.args[0].pk, self.feast.pk)
        self.mock_match.assert_not_called()
        self.mock_designation.assert_not_called()

    def test_admin_replaces_icon_with_force(self):
        self.feast.icon = self.icon
        self.feast.save(update_fields=["icon"])

        response = self._post(
            {"icon_id": self.replacement.pk, "force": True}, self.admin_user
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "REPLACED")
        self.assertEqual(response.data["current_icon_id"], self.icon.pk)
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.replacement.pk)

    def test_cross_church_icon_is_rejected(self):
        response = self._post(
            {"icon_id": self.cross_church_icon.pk}, self.admin_user
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("different church", str(response.data["detail"]))
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)

    def test_missing_icon_is_rejected(self):
        response = self._post({"icon_id": 999999}, self.admin_user)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(response.data["detail"]), "Icon 999999 does not exist.")
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)

    def test_replace_without_force_is_rejected(self):
        self.feast.icon = self.icon
        self.feast.save(update_fields=["icon"])

        response = self._post({"icon_id": self.replacement.pk}, self.admin_user)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(f"existing icon {self.icon.pk}", str(response.data["detail"]))
        self.feast.refresh_from_db()
        self.assertEqual(self.feast.icon_id, self.icon.pk)

    def test_dry_run_returns_same_shape_without_saving(self):
        with patch("hub.views.feasts.invalidate_feast_api_cache_for_feast") as invalidate:
            response = self._post(
                {"icon_id": self.icon.pk, "dry_run": True}, self.admin_user
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "WOULD-ASSIGN")
        self.assertEqual(
            set(response.data),
            {
                "status",
                "feast_id",
                "feast_name",
                "date",
                "church_id",
                "church_name",
                "current_icon_id",
                "current_icon_title",
                "requested_icon_id",
                "requested_icon_title",
            },
        )
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)
        invalidate.assert_not_called()

    def test_idempotent_retry_is_no_op_without_save(self):
        self.assertEqual(
            self._post({"icon_id": self.icon.pk}, self.admin_user).data["status"],
            "ASSIGNED",
        )

        with patch.object(Feast, "save", autospec=True) as save:
            response = self._post({"icon_id": self.icon.pk}, self.admin_user)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "NO-OP")
        save.assert_not_called()

    def test_assignment_invalidates_cache_on_commit(self):
        old_key = feast_api_cache_key(self.day.date, self.church.pk, "en")
        cache.set(old_key, "cached")

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post({"icon_id": self.icon.pk}, self.admin_user)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new_key = feast_api_cache_key(self.day.date, self.church.pk, "en")
        self.assertNotEqual(new_key, old_key)
        self.assertIsNone(cache.get(new_key))

    def test_url_resolves_under_both_mounts(self):
        for prefix in ("api", "hub"):
            match = resolve(f"/{prefix}/feasts/{self.feast.pk}/set-icon/")
            self.assertEqual(match.func.view_class, FeastSetIconView)
            self.assertEqual(match.url_name, "feast-set-icon")
