"""Tests for storage-safe media previews shared by admin pages."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from bahk.admin_media import admin_thumbnail


class BrokenMedia:
    @property
    def url(self):
        raise OSError("storage is unavailable")


class AdminThumbnailTests(SimpleTestCase):
    def test_uses_first_available_source_and_links_to_original(self):
        obj = SimpleNamespace(
            cached_thumbnail_url="https://cdn.example.test/thumb.jpg",
            image=SimpleNamespace(url="https://cdn.example.test/original.jpg"),
        )

        markup = str(
            admin_thumbnail(
                obj,
                sources=("cached_thumbnail_url", "image"),
                link_source="image",
                alt="Example image",
            )
        )

        self.assertIn('src="https://cdn.example.test/thumb.jpg"', markup)
        self.assertIn('href="https://cdn.example.test/original.jpg"', markup)
        self.assertIn('alt="Example image"', markup)
        self.assertIn('loading="lazy"', markup)
        self.assertIn('rel="noopener noreferrer"', markup)

    def test_falls_back_without_propagating_expected_storage_errors(self):
        obj = SimpleNamespace(cached_thumbnail_url="", image=BrokenMedia())

        result = admin_thumbnail(
            obj,
            sources=("cached_thumbnail_url", "image"),
            alt="Broken image",
            fallback="No image",
        )

        self.assertEqual(result, "No image")

    def test_rejects_unknown_thumbnail_sizes(self):
        with self.assertRaisesMessage(ValueError, "Unsupported admin thumbnail size"):
            admin_thumbnail(
                SimpleNamespace(image="https://cdn.example.test/image.jpg"),
                sources=("image",),
                alt="Example image",
                size="giant",
            )
