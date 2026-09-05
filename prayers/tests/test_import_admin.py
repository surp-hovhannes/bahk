from hub.services.icon_match_service import IconMatchOutcome
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

    @patch("prayers.tasks.match_icons")
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
            if "healing" in prompt.primary_text.lower() or "healing" in prompt.context_terms:
                return IconMatchOutcome(status="complete", matches=[
                    {"id": icon.id, "match_tier": "direct_exact", "confidence": "high", "auto_assignable": True}
                ])
            return IconMatchOutcome(status="complete")

        mock_match_icons.side_effect = match_side_effect

        match_icons_for_imported_prayers_task([matching_prayer.id, unmatched_prayer.id], self.church.id)

        matching_prayer.refresh_from_db()
        unmatched_prayer.refresh_from_db()
        self.assertEqual(matching_prayer.icon, icon)
        self.assertIsNone(unmatched_prayer.icon)

    @patch("prayers.tasks.match_icons")
    def test_task_sends_prayer_title_and_tags_as_prompt(self, mock_match_icons):
        """LLM prompt must contain the prayer title and tags, not just the title."""
        icon = Icon.objects.create(
            title="Cross",
            church=self.church,
            image=uploaded_image("cross.jpg"),
        )
        icon.tags.add("cross", "faith")

        prayer = Prayer.objects.create(
            title="Morning Offering",
            text="We offer this day to You.",
            category="morning",
            church=self.church,
        )
        prayer.tags.add("offering", "morning")

        mock_match_icons.return_value = IconMatchOutcome(status="complete", matches=[
            {"id": icon.id, "match_tier": "direct_exact", "confidence": "high", "auto_assignable": True}
        ])

        match_icons_for_imported_prayers_task([prayer.id], self.church.id)

        mock_match_icons.assert_called_once()
        (icons_arg, request), _ = mock_match_icons.call_args
        self.assertEqual(request.kind, "content")
        self.assertEqual(request.primary_text, "Morning Offering")
        self.assertEqual(set(request.context_terms), {"offering", "morning"})

    @patch("prayers.tasks.match_icons")
    def test_task_sends_icons_with_ids_and_tags(self, mock_match_icons):
        """LLM must receive all church icons with their IDs, titles, and tags."""
        icon1 = Icon.objects.create(
            title="Nativity",
            church=self.church,
            image=uploaded_image("nativity.jpg"),
        )
        icon1.tags.add("nativity", "christmas")
        icon2 = Icon.objects.create(
            title="Resurrection",
            church=self.church,
            image=uploaded_image("resurrection.jpg"),
        )
        icon2.tags.add("resurrection", "easter")

        prayer = Prayer.objects.create(
            title="Morning Prayer",
            text="Test.",
            category="morning",
            church=self.church,
        )

        mock_match_icons.return_value = IconMatchOutcome(status="complete", matches=[])

        match_icons_for_imported_prayers_task([prayer.id], self.church.id)

        mock_match_icons.assert_called_once()
        (icons_arg, _), _ = mock_match_icons.call_args
        icon_ids = {i.id for i in icons_arg}
        self.assertEqual(icon_ids, {icon1.id, icon2.id})

    @patch("prayers.tasks.match_icons")
    def test_task_respects_confidence_threshold(self, mock_match_icons):
        """Only icons meeting ICON_MATCH_CONFIDENCE_THRESHOLD should be assigned."""
        icon = Icon.objects.create(
            title="Cross",
            church=self.church,
            image=uploaded_image("cross.jpg"),
        )

        prayer = Prayer.objects.create(
            title="Morning Prayer",
            text="Test.",
            category="morning",
            church=self.church,
        )

        # Return "low" confidence — should be below default threshold ("medium")
        mock_match_icons.return_value = IconMatchOutcome(status="complete", matches=[
            {"id": icon.id, "match_tier": "thematic", "confidence": "low"}
        ])

        match_icons_for_imported_prayers_task([prayer.id], self.church.id)

        prayer.refresh_from_db()
        self.assertIsNone(prayer.icon, "Low-confidence match should not be assigned")

    @patch("prayers.tasks.match_icons")
    def test_task_does_not_persist_related_specific_medium(self, mock_match_icons):
        icon = Icon.objects.create(
            title="St. Mesrop Mashtots",
            church=self.church,
            image=uploaded_image("mesrop.jpg"),
        )
        prayer = Prayer.objects.create(
            title="Prayer of the Holy Translators",
            text="Grant us wisdom.",
            category="general",
            church=self.church,
        )
        mock_match_icons.return_value = IconMatchOutcome(status="complete", matches=[
            {"id": icon.id, "match_tier": "related_specific", "confidence": "medium"}
        ])

        match_icons_for_imported_prayers_task([prayer.id], self.church.id)

        prayer.refresh_from_db()
        self.assertIsNone(prayer.icon)

    @patch("prayers.tasks.match_icons")
    def test_task_returns_early_when_no_icons_exist(self, mock_match_icons):
        """Task should return early without calling LLM when church has no icons."""
        prayer = Prayer.objects.create(
            title="Morning Prayer",
            text="Test.",
            category="morning",
            church=self.church,
        )

        match_icons_for_imported_prayers_task([prayer.id], self.church.id)

        mock_match_icons.assert_not_called()
