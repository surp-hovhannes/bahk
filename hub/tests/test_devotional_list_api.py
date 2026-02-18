from datetime import date, timedelta

from django.test import TestCase
from rest_framework import status
from rest_framework.filters import BaseFilterBackend
from unittest.mock import patch

from hub.models import Church, Day, Devotional, Fast
from hub.views.devotionals import DevotionalListView
from learning_resources.models import Video


class OrderByDateFilterBackend(BaseFilterBackend):
    """Test backend that forces ordering to validate filter pipeline behavior."""

    def filter_queryset(self, request, queryset, view):
        return queryset.order_by("-day__date")


class DevotionalListAPITests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Church A")
        self.fast = Fast.objects.create(
            name="Great Lent",
            church=self.church,
            description="Fast description",
            culmination_feast="Pascha",
        )
        self.other_church = Church.objects.create(name="Church B")
        self.other_fast = Fast.objects.create(
            name="Other Fast",
            church=self.other_church,
            description="Other description",
            culmination_feast="Other culmination",
        )

        base_date = date(2026, 1, 1)
        devotional_seed = [
            ("Opening Prayer", "General guide", "Start of series"),
            ("Mercy and Hope", "General guide", "Reflection text"),
            ("Daily Reading", "FASTING habits explained", "Routine"),
            ("Lent Steps Part 1", "Warmup", "Lent basics"),
            ("Lent Steps Part 2", "Warmup", "Deep reflection"),
            ("Closing Day", "Final wrap-up", "Finish line"),
        ]

        for index, (title, video_description, devotional_description) in enumerate(
            devotional_seed
        ):
            day = Day.objects.create(
                date=base_date + timedelta(days=index),
                fast=self.fast,
                church=self.church,
            )
            video = Video.objects.create(
                title=title,
                description=video_description,
                category="devotional",
                language_code="en",
            )
            Devotional.objects.create(
                day=day,
                description=devotional_description,
                video=video,
                order=1,
                language_code="en",
            )

        other_day = Day.objects.create(
            date=date(2026, 1, 10),
            fast=self.other_fast,
            church=self.other_church,
        )
        other_video = Video.objects.create(
            title="Lent for Other Church",
            description="Should not appear in Church A results",
            category="devotional",
            language_code="en",
        )
        Devotional.objects.create(
            day=other_day,
            description="Other church devotional",
            video=other_video,
            order=1,
            language_code="en",
        )

    def _get_list(self, **params):
        query = {"church_id": self.church.id, **params}
        return self.client.get("/api/devotionals/", query)

    def test_devotional_list_default_behavior_still_works(self):
        response = self._get_list()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("count", response.data)
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertIn("results", response.data)
        self.assertEqual(response.data["count"], 6)
        self.assertEqual(response.data["results"][0]["date"], "2026-01-01")

    def test_recent_devotionals_ordering_and_limit(self):
        response = self._get_list(ordering="-day__date", limit=5)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertEqual(
            [item["date"] for item in response.data["results"]],
            ["2026-01-06", "2026-01-05", "2026-01-04", "2026-01-03", "2026-01-02"],
        )

        alias_response = self._get_list(ordering="-date", limit=5)
        self.assertEqual(alias_response.status_code, status.HTTP_200_OK)
        self.assertEqual(alias_response.data["count"], 5)
        self.assertEqual(
            [item["date"] for item in alias_response.data["results"]],
            ["2026-01-06", "2026-01-05", "2026-01-04", "2026-01-03", "2026-01-02"],
        )

    def test_search_is_case_insensitive_across_video_and_devotional_text(self):
        video_response = self._get_list(search="fAsTiNg")
        self.assertEqual(video_response.status_code, status.HTTP_200_OK)
        self.assertEqual(video_response.data["count"], 1)
        self.assertEqual(video_response.data["results"][0]["title"], "Daily Reading")

        devotional_response = self._get_list(search="finish")
        self.assertEqual(devotional_response.status_code, status.HTTP_200_OK)
        self.assertEqual(devotional_response.data["count"], 1)
        self.assertEqual(devotional_response.data["results"][0]["title"], "Closing Day")

    def test_search_and_ordering_can_be_combined(self):
        response = self._get_list(search="lent", ordering="-day__date")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [item["title"] for item in response.data["results"]],
            ["Lent Steps Part 2", "Lent Steps Part 1"],
        )

    def test_limit_works_with_filter_backends_ordering(self):
        with patch.object(DevotionalListView, "filter_backends", [OrderByDateFilterBackend]):
            response = self._get_list(limit=3)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(len(response.data["results"]), 3)
        self.assertEqual(
            [item["date"] for item in response.data["results"]],
            ["2026-01-06", "2026-01-05", "2026-01-04"],
        )
