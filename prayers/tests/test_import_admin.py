"""Tests for the prayer set import admin flow and icon matching task."""

import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from hub.models import Church
from icons.models import Icon
from prayers.models import Prayer, PrayerSet, PrayerSetMembership
from prayers.tasks import match_icons_for_imported_prayers_task


def import_payload():
    return {
        "prayer_sets": [
            {
                "title": "Admin Import Set",
                "description": "Imported from admin",
                "category": "general",
                "prayers": [
                    {
                        "title": "Admin First Prayer",
                        "text": "First text",
                        "category": "morning",
                        "tags": ["morning"],
                    },
                    {
                        "title": "Admin Second Prayer",
                        "text": "Second text",
                        "category": "evening",
                        "tags": ["evening"],
                    },
                ],
            },
        ],
    }


def uploaded_json(data):
    return SimpleUploadedFile(
        "prayer_sets.json",
        json.dumps(data).encode("utf-8"),
        content_type="application/json",
    )


def uploaded_image(name="icon.jpg"):
    image = Image.new("RGB", (10, 10), color="blue")
    image_io = io.BytesIO()
    image.save(image_io, "JPEG")
    image_io.seek(0)
    return SimpleUploadedFile(name, image_io.read(), content_type="image/jpeg")


class PrayerImportAdminTests(TestCase):
    """Integration coverage for admin import preview and confirmation."""

    def setUp(self):
        self.church = Church.objects.create(name="Admin Import Church")
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.admin_user)

    def test_admin_import_flow_preview_confirm_db_state(self):
        preview_response = self.client.post(
            reverse("admin:prayers_import"),
            {
                "church": self.church.id,
                "json_file": uploaded_json(import_payload()),
            },
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "Preview import for Admin Import Church")
        self.assertContains(preview_response, "1 prayer set(s), 2 prayer(s)")

        confirm_response = self.client.post(reverse("admin:prayers_import_confirm"))

        self.assertRedirects(confirm_response, reverse("admin:prayers_prayerset_changelist"))
        prayer_set = PrayerSet.objects.get(title="Admin Import Set", church=self.church)
        self.assertEqual(prayer_set.prayers.count(), 2)
        self.assertEqual(
            list(
                PrayerSetMembership.objects.filter(prayer_set=prayer_set)
                .order_by("order")
                .values_list("prayer__title", flat=True)
            ),
            ["Admin First Prayer", "Admin Second Prayer"],
        )


class MatchIconsForImportedPrayersTaskTests(TestCase):
    """Icon matching task should assign the highest scoring church icon."""

    def setUp(self):
        self.church = Church.objects.create(name="Task Church")

    @patch("prayers.tasks._match_icons_with_llm")
    def test_task_assigns_matching_icon_and_leaves_unmatched_prayer(self, mock_match_icons):
        icon = Icon.objects.create(
            title="Healing Christ",
            church=self.church,
            image=uploaded_image("healing.jpg"),
        )
        icon.tags.add("healing")

        matching_prayer = Prayer.objects.create(
            title="Healing Prayer",
            text="Lord, heal us.",
            category="general",
            church=self.church,
        )
        matching_prayer.tags.add("healing")
        unmatched_prayer = Prayer.objects.create(
            title="Travel Prayer",
            text="Lord, guide our trip.",
            category="general",
            church=self.church,
        )

        def match_side_effect(icons, prompt, max_results=1):
            if "healing" in prompt.lower():
                return [{"id": icon.id, "confidence": "high"}]
            return []

        mock_match_icons.side_effect = match_side_effect

        match_icons_for_imported_prayers_task([matching_prayer.id, unmatched_prayer.id], self.church.id)

        matching_prayer.refresh_from_db()
        unmatched_prayer.refresh_from_db()
        self.assertEqual(matching_prayer.icon, icon)
        self.assertIsNone(unmatched_prayer.icon)
