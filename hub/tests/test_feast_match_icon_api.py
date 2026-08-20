"""Tests for the feast icon match trigger API."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from hub.models import Church, Day, Feast
from icons.models import Icon


class FeastMatchIconAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="password",
            is_staff=True,
        )
        self.regular_user = user_model.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="password",
        )
        self.client = APIClient()
        self.church = Church.objects.get(pk=Church.get_default_pk())

        self.day_without_icon = Day.objects.create(
            date=date(2026, 6, 8),
            church=self.church,
        )
        self.day_with_icon = Day.objects.create(
            date=date(2026, 6, 9),
            church=self.church,
        )
        image = SimpleUploadedFile(
            name="test_icon.jpg",
            content=b"fake image content",
            content_type="image/jpeg",
        )
        self.icon = Icon.objects.create(
            title="Existing Icon",
            church=self.church,
            image=image,
        )

        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast_without_icon = Feast.objects.create(
                church=self.day_without_icon.church,
                name="Feast Without Icon",
            )
            self.feast_with_icon = Feast.objects.create(
                church=self.day_with_icon.church,
                name="Feast With Icon",
                icon=self.icon,
            )

    def test_staff_post_enqueues_when_icon_missing(self):
        self.client.force_authenticate(user=self.staff_user)
        url = reverse("feast-match-icon", kwargs={"feast_id": self.feast_without_icon.pk})

        with patch("hub.views.feasts.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "status": "enqueued",
                "feast_id": self.feast_without_icon.pk,
                "reason": "icon missing",
            },
        )
        mock_delay.assert_called_once_with(self.feast_without_icon.pk)

    def test_staff_post_skips_when_icon_present(self):
        self.client.force_authenticate(user=self.staff_user)
        url = reverse("feast-match-icon", kwargs={"feast_id": self.feast_with_icon.pk})

        with patch("hub.views.feasts.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "status": "skipped",
                "feast_id": self.feast_with_icon.pk,
                "reason": "icon already present",
            },
        )
        mock_delay.assert_not_called()

    def test_non_staff_post_forbidden(self):
        self.client.force_authenticate(user=self.regular_user)
        url = reverse("feast-match-icon", kwargs={"feast_id": self.feast_without_icon.pk})

        with patch("hub.views.feasts.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_delay.assert_not_called()

    def test_anonymous_post_requires_authentication(self):
        url = reverse("feast-match-icon", kwargs={"feast_id": self.feast_without_icon.pk})

        with patch("hub.views.feasts.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        mock_delay.assert_not_called()

    def test_missing_feast_returns_404_for_staff(self):
        self.client.force_authenticate(user=self.staff_user)
        url = reverse("feast-match-icon", kwargs={"feast_id": 999999})

        with patch("hub.views.feasts.match_icon_to_feast_task.delay") as mock_delay:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_delay.assert_not_called()
