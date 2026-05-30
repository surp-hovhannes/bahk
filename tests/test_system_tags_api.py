"""Tests for the cross-app system tags API."""

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church, PatristicQuote
from icons.models import Icon
from prayers.models import Prayer


class SystemTagsAPITests(APITestCase):
    """Test /api/tags/ responses across taggable models."""

    url = "/api/tags/"

    def setUp(self):
        self.church = Church.objects.create(name="Test Church")

    def create_prayer(self, title, tags):
        prayer = Prayer.objects.create(
            title=title,
            text=f"{title} text.",
            category="general",
            church=self.church,
        )
        prayer.tags.add(*tags)
        return prayer

    def create_icon(self, title, tags):
        icon = Icon.objects.create(
            title=title,
            church=self.church,
            image=SimpleUploadedFile(
                name=f"{title.lower().replace(' ', '_')}.jpg",
                content=b"fake image content",
                content_type="image/jpeg",
            ),
        )
        icon.tags.add(*tags)
        return icon

    def create_quote(self, attribution, tags):
        quote = PatristicQuote.objects.create(
            text=f"Quote from {attribution}.",
            attribution=attribution,
        )
        quote.churches.add(self.church)
        quote.tags.add(*tags)
        return quote

    def test_public_endpoint_requires_no_auth(self):
        self.create_prayer("Morning Prayer", ["daily"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_public_endpoint_ignores_invalid_auth_header(self):
        self.create_prayer("Morning Prayer", ["daily"])

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION="Bearer not-a-valid-token",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["prayer"], ["daily"])

    def test_single_model_returns_flat_sorted_unique_array(self):
        self.create_prayer("Morning Prayer", ["Zebra", "alpha", "daily"])
        self.create_prayer("Evening Prayer", ["daily", "Beta"])
        self.create_icon("Cross Icon", ["cross"])

        response = self.client.get(self.url, {"model": "prayer"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, ["alpha", "Beta", "daily", "Zebra"])

    def test_multi_model_returns_grouped_object(self):
        self.create_prayer("Morning Prayer", ["daily"])
        self.create_icon("Cross Icon", ["cross"])
        self.create_quote("St. Basil", ["humility"])

        response = self.client.get(self.url, {"model": "prayer,icon"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data.keys()), {"prayer", "icon"})
        self.assertEqual(response.data["prayer"], ["daily"])
        self.assertEqual(response.data["icon"], ["cross"])

    def test_all_models_returns_grouped_object(self):
        self.create_prayer("Morning Prayer", ["daily"])
        self.create_icon("Cross Icon", ["cross"])
        self.create_quote("St. Basil", ["humility"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "prayer": ["daily"],
                "icon": ["cross"],
                "patristic_quote": ["humility"],
            },
        )

    def test_model_param_normalizes_whitespace_case_and_duplicates(self):
        self.create_prayer("Morning Prayer", ["daily"])
        self.create_icon("Cross Icon", ["cross"])

        response = self.client.get(self.url, {"model": " Prayer,ICON,prayer"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data.keys()), ["prayer", "icon"])
        self.assertEqual(response.data["prayer"], ["daily"])
        self.assertEqual(response.data["icon"], ["cross"])

    def test_patristic_quote_alias_returns_canonical_flat_response(self):
        self.create_quote("St. Basil", ["humility"])

        response = self.client.get(self.url, {"model": "patristicquote"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, ["humility"])

    def test_invalid_model_returns_400(self):
        response = self.client.get(self.url, {"model": "prayer,unknown"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "Unsupported model filter: unknown")
        self.assertEqual(
            response.data["allowed_models"],
            ["prayer", "icon", "patristic_quote"],
        )

    def test_empty_model_has_empty_array(self):
        self.create_prayer("Morning Prayer", ["daily"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["prayer"], ["daily"])
        self.assertEqual(response.data["icon"], [])
        self.assertEqual(response.data["patristic_quote"], [])

    def test_blank_model_filter_returns_all_models(self):
        self.create_prayer("Morning Prayer", ["daily"])

        response = self.client.get(f"{self.url}?model=")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {"prayer", "icon", "patristic_quote"},
        )
