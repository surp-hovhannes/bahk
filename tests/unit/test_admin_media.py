"""Tests for storage-safe media previews shared by admin pages."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from bahk.admin_media import admin_thumbnail, admin_video_player


class BrokenMedia:
    @property
    def url(self):
        raise OSError("storage is unavailable")


class AbsoluteUrlMedia:
    url = "/media/https%3A/cdn.example.test/video.mp4"

    def __str__(self):
        return "https://cdn.example.test/video.mp4"


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


class AdminVideoPlayerTests(SimpleTestCase):
    def test_renders_video_controls_poster_and_direct_link(self):
        obj = SimpleNamespace(
            video=SimpleNamespace(url="https://cdn.example.test/video.mp4"),
            cached_thumbnail_url="https://cdn.example.test/poster.jpg",
        )

        markup = str(
            admin_video_player(
                obj,
                source="video",
                title="Example video",
                poster_sources=("cached_thumbnail_url",),
            )
        )

        self.assertIn("<video", markup)
        self.assertIn(" controls", markup)
        self.assertIn('preload="metadata"', markup)
        self.assertIn('poster="https://cdn.example.test/poster.jpg"', markup)
        self.assertIn('src="https://cdn.example.test/video.mp4"', markup)
        self.assertIn('href="https://cdn.example.test/video.mp4"', markup)
        self.assertIn('aria-label="Preview: Example video"', markup)

    def test_falls_back_when_video_is_unavailable(self):
        result = admin_video_player(
            SimpleNamespace(video=BrokenMedia()),
            source="video",
            title="Broken video",
            fallback="No preview",
        )

        self.assertEqual(result, "No preview")

    def test_supports_file_fields_whose_stored_name_is_an_absolute_url(self):
        markup = str(
            admin_video_player(
                SimpleNamespace(video=AbsoluteUrlMedia()),
                source="video",
                title="Remote video",
            )
        )

        self.assertIn('src="https://cdn.example.test/video.mp4"', markup)
