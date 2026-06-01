"""Tests for icon API view-level caching."""

from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church
from icons.cache import IconViewCache
from icons.models import Icon
from icons.views import IconListView, IconMatchView


TEST_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "icon-cache-tests",
    }
}


@override_settings(CACHES=TEST_CACHE, OPENAI_API_KEY="")
class IconViewCachingTests(APITestCase):
    """Focused cache behavior tests for the icon API endpoints."""

    def setUp(self):
        cache.clear()
        self.church = Church.objects.create(name="Test Church")
        self.other_church = Church.objects.create(name="Other Church")
        self.icon1 = self._create_icon("Nativity Icon", self.church, "nativity")
        self.icon1.tags.add("nativity", "christmas")
        self.icon2 = self._create_icon("Cross Icon", self.church, "cross")
        self.icon2.tags.add("cross", "lent")
        self.icon3 = self._create_icon("Other Nativity", self.other_church, "other")
        self.icon3.tags.add("nativity")
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _create_icon(self, title, church, filename_prefix):
        image = SimpleUploadedFile(
            name=f"{filename_prefix}.jpg",
            content=b"fake image content",
            content_type="image/jpeg",
        )
        return Icon.objects.create(title=title, church=church, image=image)

    def test_list_cache_key_uses_stable_query_param_hash(self):
        params_a = QueryDict(f"church={self.church.id}&search=nativity")
        params_b = QueryDict(f"search=nativity&church={self.church.id}")
        params_c = QueryDict(f"search=cross&church={self.church.id}")

        self.assertEqual(
            IconViewCache.list_key(params_a),
            IconViewCache.list_key(params_b),
        )
        self.assertNotEqual(
            IconViewCache.list_key(params_a),
            IconViewCache.list_key(params_c),
        )

    def test_list_caches_successful_response(self):
        first = self.client.get("/api/icons/")
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        with patch.object(
            IconListView,
            "get_queryset",
            side_effect=AssertionError("list cache was not used"),
        ):
            second = self.client.get("/api/icons/")

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)

    def test_list_cache_varies_by_query_params(self):
        nativity = self.client.get("/api/icons/?search=nativity")
        cross = self.client.get("/api/icons/?search=cross")
        tagged = self.client.get("/api/icons/?tags=nativity&page=1")

        self.assertEqual(nativity.status_code, status.HTTP_200_OK)
        self.assertEqual(cross.status_code, status.HTTP_200_OK)
        self.assertEqual(tagged.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["title"] for item in nativity.data["results"]},
            {"Nativity Icon", "Other Nativity"},
        )
        self.assertEqual(
            {item["title"] for item in cross.data["results"]},
            {"Cross Icon"},
        )
        self.assertEqual(
            {item["title"] for item in tagged.data["results"]},
            {"Nativity Icon", "Other Nativity"},
        )

    def test_detail_cache_invalidates_after_icon_update(self):
        first = self.client.get(f"/api/icons/{self.icon1.id}/")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["title"], "Nativity Icon")

        self.icon1.title = "Updated Nativity Icon"
        self.icon1.save(update_fields=["title"])

        second = self.client.get(f"/api/icons/{self.icon1.id}/")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["title"], "Updated Nativity Icon")

    def test_detail_does_not_cache_404(self):
        missing_id = self.icon3.id + 1000
        missing = self.client.get(f"/api/icons/{missing_id}/")
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

        created = self._create_icon("Late Icon", self.church, "late")
        Icon.objects.filter(pk=created.pk).update(id=missing_id)

        found = self.client.get(f"/api/icons/{missing_id}/")
        self.assertEqual(found.status_code, status.HTTP_200_OK)
        self.assertEqual(found.data["title"], "Late Icon")

    def test_match_caches_by_normalized_prompt_and_max_results(self):
        first = self.client.post(
            "/api/icons/match/",
            {
                "prompt": " nativity ",
                "return_format": "id",
                "max_results": 1,
            },
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        with patch.object(
            IconMatchView,
            "_simple_match_icons",
            side_effect=AssertionError("match cache was not used"),
        ):
            second = self.client.post(
                "/api/icons/match/",
                {
                    "prompt": "nativity",
                    "return_format": "id",
                    "max_results": "1",
                },
                format="json",
            )

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)

    def test_match_cache_varies_by_return_format_and_church(self):
        id_response = self.client.post(
            "/api/icons/match/",
            {
                "prompt": "nativity",
                "return_format": "id",
                "max_results": 1,
                "church_id": self.church.id,
            },
            format="json",
        )
        full_response = self.client.post(
            "/api/icons/match/",
            {
                "prompt": "nativity",
                "return_format": "full",
                "max_results": 1,
                "church_id": self.other_church.id,
            },
            format="json",
        )

        self.assertEqual(id_response.status_code, status.HTTP_200_OK)
        self.assertEqual(full_response.status_code, status.HTTP_200_OK)
        self.assertNotIn("icon", id_response.data["matches"][0])
        self.assertEqual(
            full_response.data["matches"][0]["icon"]["title"],
            "Other Nativity",
        )

    def test_tag_changes_invalidate_list_cache(self):
        first = self.client.get("/api/icons/?tags=saint")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["results"], [])

        self.icon1.tags.add("saint")

        second = self.client.get("/api/icons/?tags=saint")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["title"] for item in second.data["results"]],
            ["Nativity Icon"],
        )
