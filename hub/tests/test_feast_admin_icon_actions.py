"""Tests for Feast admin icon rematch actions/endpoints."""

from datetime import date
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from hub.admin import FeastAdmin
from hub.models import Church, Day, Feast
from icons.models import Icon


class FeastAdminIconActionsTests(TestCase):
    def _admin_request(self):
        """
        Return a RequestFactory request wired with session + message storage,
        matching what admin actions expect.
        """
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = self.admin_user

        # Add session support
        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session.save()

        # Add message storage
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )

        self.church = Church.objects.get(pk=Church.get_default_pk())

        self.day_with_icon = Day.objects.create(
            date=date(2025, 12, 25),
            church=self.church,
        )
        self.day_without_icon = Day.objects.create(
            date=date(2025, 12, 26),
            church=self.church,
        )

        test_image = SimpleUploadedFile(
            name="test_icon.jpg",
            content=b"fake image content",
            content_type="image/jpeg",
        )
        self.icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image,
        )

        # Avoid enqueuing icon matching from Feast post_save signal during setup.
        with patch("hub.signals.match_icon_to_feast_task.delay"):
            self.feast_with_icon = Feast.objects.create(
                day=self.day_with_icon,
                name="Christmas",
                icon=self.icon,
            )
            self.feast_without_icon = Feast.objects.create(
                day=self.day_without_icon,
                name="Feast Without Icon",
            )

    def test_force_rematch_icon_action_clears_and_enqueues(self):
        request = self._admin_request()

        feast_admin = FeastAdmin(Feast, AdminSite())
        queryset = Feast.objects.filter(
            pk__in=[self.feast_with_icon.pk, self.feast_without_icon.pk]
        )

        with patch("hub.admin.match_icon_to_feast_task.delay") as mock_delay:
            feast_admin.force_rematch_icon(request, queryset)

        self.feast_with_icon.refresh_from_db()
        self.feast_without_icon.refresh_from_db()

        self.assertIsNone(self.feast_with_icon.icon)
        self.assertIsNone(self.feast_without_icon.icon)
        self.assertEqual(mock_delay.call_count, 2)

    def test_match_icon_if_missing_action_enqueues_only_when_missing(self):
        request = self._admin_request()

        feast_admin = FeastAdmin(Feast, AdminSite())
        queryset = Feast.objects.filter(
            pk__in=[self.feast_with_icon.pk, self.feast_without_icon.pk]
        )

        with patch("hub.admin.match_icon_to_feast_task.delay") as mock_delay:
            feast_admin.match_icon_if_missing(request, queryset)

        mock_delay.assert_called_once_with(self.feast_without_icon.pk)

    def test_rematch_icon_force_view_clears_and_enqueues(self):
        self.client.force_login(self.admin_user)

        url = reverse(
            "admin:hub_feast_rematch_icon_force", args=[self.feast_with_icon.pk]
        )
        with patch("hub.admin.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.feast_with_icon.refresh_from_db()
        self.assertIsNone(self.feast_with_icon.icon)
        mock_delay.assert_called_once_with(self.feast_with_icon.pk)

    def test_rematch_icon_if_missing_view_skips_when_icon_present(self):
        self.client.force_login(self.admin_user)

        url = reverse(
            "admin:hub_feast_rematch_icon_if_missing",
            args=[self.feast_with_icon.pk],
        )
        with patch("hub.admin.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_not_called()


