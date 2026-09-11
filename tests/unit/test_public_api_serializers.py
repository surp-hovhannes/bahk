"""Contract tests for the public v1 API serializers.

Issue #497 -- presentation-neutral serializers. These tests pin the exact
shape (field set, types, nullability, localization) the public v1 contract
promises. They must:

* assert the exact decoded JSON keys and value types/nulls
* exercise both the supported-language and canonical/base-language fallback
  paths for user-facing text
* exercise both annotated and unannotated Fast date behavior (no related-day
  queries during serialization)
* assert citation-only fields for Reading
* exercise the nested Feast Icon present/absent paths
* exercise media mappings (cached thumbnails, original images, learn_more_url)
* pin that every public serializer refuses create()/update()/save()
* pin the queryset preconditions: ``with_dates()`` Fast dates and
  ``select_related("icon")`` Feast icons serialize with zero extra queries
* pin that serializer context — not the request — resolves the language,
  and that unknown languages fall back to canonical values
* verify excluded fields never leak into the public response
* verify that ``thumbnail_url`` never accesses ``ImageSpecField.url`` or
  triggers thumbnail generation / cache writes / model writes

Tests intentionally avoid spinning up the public HTTP views because no public
resource routes are mounted yet (issue #497 covers serializers only; route
mounting is gated by #494, #496, #497, #498, #499, and #500). The
serializers are exercised directly against ``Fast``, ``Church``, ``Reading``,
``Feast``, and ``Icon`` model instances, which is what an eventual public
view would do anyway.
"""

import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import translation
from PIL import Image

from bahk.public_api.v1.serializers import (
    ChurchPublicSerializer,
    FastPublicSerializer,
    FeastPublicSerializer,
    IconPublicSerializer,
    ReadingPublicSerializer,
    _cached_thumbnail_url,
    _image_url,
)
from hub.models import Church, Day, Fast, Feast, Reading
from icons.models import Icon


def _png_upload(name="sample.png", size=(8, 8), color=(255, 0, 0)):
    """Build a SimpleUploadedFile containing a real PNG.

    The Icon/Fast ``save()`` paths may invoke thumbnail generation and image
    fingerprinting; supplying a real image lets the existing save() logic
    complete without exceptions, keeping tests focused on serializer
    behavior rather than model internals.
    """
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name=name, content=buffer.getvalue(), content_type="image/png")


# ---------------------------------------------------------------------------
# Church
# ---------------------------------------------------------------------------


class ChurchPublicSerializerContractTests(TestCase):
    """Public Church serializer: ``id``, ``name`` only."""

    def test_returns_exact_id_and_name_keys(self):
        church = Church.objects.create(name="Test Church")

        data = ChurchPublicSerializer(church).data

        self.assertEqual(set(data.keys()), {"id", "name"})
        self.assertEqual(data["id"], church.id)
        self.assertIsInstance(data["id"], int)
        self.assertEqual(data["name"], "Test Church")
        self.assertIsInstance(data["name"], str)


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------


class IconPublicSerializerContractTests(TestCase):
    """Public Icon serializer: ``id``, ``title``, ``image_url``, ``thumbnail_url``."""

    def setUp(self):
        self.church = Church.objects.create(name="Icon Church")

    def _create_icon(self, **kwargs):
        """Create an Icon with predictable state for serializer tests.

        We deliberately do NOT pre-set ``cached_thumbnail_url`` in the
        factory because ``Icon.save()`` auto-populates it from the spec
        after a successful upload. Tests that care about a specific cache
        value should set it via ``Icon.objects.filter(...).update(...)``
        *after* creation, which mirrors the realistic sequence: an icon
        is created, the post-save hook writes the cache, then a public
        request reads it.
        """
        defaults = {
            "title": "Test Icon",
            "church": self.church,
            "image": _png_upload("icon.png"),
        }
        defaults.update(kwargs)
        return Icon.objects.create(**defaults)

    def test_returns_exact_id_title_image_url_thumbnail_url_keys(self):
        icon = self._create_icon(title="Nativity")
        # Pin a deterministic cached URL after the post-save hook has run.
        Icon.objects.filter(pk=icon.pk).update(cached_thumbnail_url="https://cdn.example.com/icon-thumb.jpg")
        icon.refresh_from_db()

        data = IconPublicSerializer(icon).data

        self.assertEqual(
            set(data.keys()),
            {"id", "title", "image_url", "thumbnail_url"},
        )
        self.assertEqual(data["id"], icon.id)
        self.assertIsInstance(data["id"], int)
        self.assertEqual(data["title"], "Nativity")
        self.assertIsInstance(data["title"], str)
        self.assertIsInstance(data["image_url"], str)
        self.assertEqual(
            data["thumbnail_url"],
            "https://cdn.example.com/icon-thumb.jpg",
        )

    def test_excludes_church_admin_and_metadata_fields(self):
        icon = self._create_icon()

        data = IconPublicSerializer(icon).data

        for excluded in (
            "church",
            "church_id",
            "tags",
            "tag_list",
            "image",
            "image_hash",
            "phash",
            "image_content_digest",
            "image_revision",
            "original_filename",
            "filename_provenance",
            "created_at",
            "updated_at",
            "cached_thumbnail_url",
            "cached_thumbnail_updated",
            "thumbnail",
        ):
            self.assertNotIn(excluded, data)

    def test_thumbnail_url_is_null_when_no_cache_present(self):
        icon = self._create_icon()
        # Force the cache to be empty so the null-branch is exercised
        # even though the post-save hook auto-populated it.
        Icon.objects.filter(pk=icon.pk).update(cached_thumbnail_url=None)
        icon.refresh_from_db()
        self.assertFalse(icon.cached_thumbnail_url)

        data = IconPublicSerializer(icon).data

        self.assertIsNone(data["thumbnail_url"])

    def test_thumbnail_url_does_not_access_imagespecfield_url(self):
        """Sentinel: serialization MUST NOT read the ImageSpecField URL.

        Accessing ``icon.thumbnail.url`` would invoke the imagekit spec,
        which can hit S3 and generate a thumbnail. We verify the
        serialized value comes purely from the cached column by clearing
        the cache and patching the descriptor to raise on access.
        """
        icon = self._create_icon()
        Icon.objects.filter(pk=icon.pk).update(cached_thumbnail_url=None)
        icon.refresh_from_db()

        class _Forbidden(Exception):
            pass

        def _explode(_self):
            raise _Forbidden("Public Icon serializer must not read ImageSpecField.url")

        with patch.object(type(icon), "thumbnail", new=property(_explode)):
            data = IconPublicSerializer(icon).data

        self.assertIsNone(data["thumbnail_url"])

    def test_image_url_is_set_when_image_uploaded(self):
        """Confirm image_url resolves to a non-null string for an Icon
        with an uploaded image. The companion null path is exercised by
        ``MediaHelperTests.test_image_url_returns_none_when_no_image``.
        """
        icon = self._create_icon()
        data = IconPublicSerializer(icon).data
        self.assertIsInstance(data["image_url"], str)


# ---------------------------------------------------------------------------
# Reading (citation-only)
# ---------------------------------------------------------------------------


class ReadingPublicSerializerContractTests(TestCase):
    """Public Reading serializer: citation only, no text or context."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=datetime.date(2026, 3, 1), church=self.church)

    def _make_reading(self, **kwargs):
        defaults = dict(
            day=self.day,
            sequence=1,
            book="Genesis",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
            text="LEGACY text that must not appear",
            text_copyright="LEGACY copyright",
            text_version="LEGACY",
            text_fetched_at=datetime.datetime.now(tz=datetime.timezone.utc),
            fums_token="LEGACY",
        )
        defaults.update(kwargs)
        return Reading.objects.create(**defaults)

    def test_returns_exact_citation_keys(self):
        reading = self._make_reading()

        data = ReadingPublicSerializer(reading).data

        self.assertEqual(
            set(data.keys()),
            {"id", "sequence", "book", "start_chapter", "start_verse", "end_chapter", "end_verse"},
        )
        self.assertEqual(data["id"], reading.id)
        self.assertIsInstance(data["id"], int)
        self.assertEqual(data["sequence"], 1)
        self.assertIsInstance(data["sequence"], int)
        self.assertEqual(data["book"], "Genesis")
        self.assertEqual(data["start_chapter"], 1)
        self.assertEqual(data["start_verse"], 1)
        self.assertEqual(data["end_chapter"], 1)
        self.assertEqual(data["end_verse"], 5)

    def test_sequence_serializes_null_when_unset(self):
        reading = self._make_reading(sequence=None)

        data = ReadingPublicSerializer(reading).data

        self.assertIsNone(data["sequence"])

    def test_empty_book_serializes_as_null(self):
        reading = self._make_reading(book="")

        data = ReadingPublicSerializer(reading).data

        self.assertIsNone(data["book"])

    def test_excludes_text_context_and_fetch_fields(self):
        reading = self._make_reading()

        data = ReadingPublicSerializer(reading).data

        for excluded in (
            "text",
            "text_copyright",
            "text_version",
            "text_fetched_at",
            "fums_token",
            "text_hy",
            "text_hy_copyright",
            "text_hy_version",
            "text_hy_fetched_at",
            "text_hy_fums_token",
            "passage_key",
            "day",
        ):
            self.assertNotIn(excluded, data)


# ---------------------------------------------------------------------------
# Feast (with optional nested Icon)
# ---------------------------------------------------------------------------


class FeastPublicSerializerContractTests(TestCase):
    """Public Feast serializer: ``id``, ``name``, nested nullable ``icon``."""

    def setUp(self):
        self.church = Church.objects.create(name="Feast Church")

    def test_returns_id_name_and_null_icon_when_no_icon_attached(self):
        feast = Feast.objects.create(church=self.church, name="Easter")

        data = FeastPublicSerializer(feast).data

        self.assertEqual(set(data.keys()), {"id", "name", "icon"})
        self.assertEqual(data["id"], feast.id)
        self.assertEqual(data["name"], "Easter")
        self.assertIsNone(data["icon"])

    def test_excludes_designation_context_and_church_fields(self):
        feast = Feast.objects.create(
            church=self.church,
            name="Christmas",
            designation=Feast.Designation.NATIVITY_MOTHER_OF_GOD,
        )

        data = FeastPublicSerializer(feast).data

        for excluded in (
            "church",
            "church_id",
            "designation",
            "icon_id",
        ):
            self.assertNotIn(excluded, data)

    def test_returns_nested_icon_object_when_attached(self):
        icon = Icon.objects.create(
            title="Nativity",
            church=self.church,
            image=_png_upload("nativity.png"),
        )
        # ``Icon.save()`` overwrites ``cached_thumbnail_url`` from
        # ``ImageSpecField.url`` when an image is uploaded, so the
        # supplied value cannot survive ``create()``. Pin a deterministic
        # cached URL via queryset update after the post-save hook has
        # run, mirroring the Icon serializer tests.
        Icon.objects.filter(pk=icon.pk).update(cached_thumbnail_url="https://cdn.example.com/nativity-thumb.jpg")
        icon.refresh_from_db()
        feast = Feast.objects.create(church=self.church, name="Christmas", icon=icon)

        data = FeastPublicSerializer(feast).data

        self.assertEqual(
            set(data["icon"].keys()),
            {"id", "title", "image_url", "thumbnail_url"},
        )
        self.assertEqual(data["icon"]["id"], icon.id)
        self.assertEqual(data["icon"]["title"], "Nativity")
        self.assertIsInstance(data["icon"]["image_url"], str)
        self.assertEqual(
            data["icon"]["thumbnail_url"],
            "https://cdn.example.com/nativity-thumb.jpg",
        )

    def test_empty_name_serializes_as_null(self):
        feast = Feast.objects.create(church=self.church, name="")

        data = FeastPublicSerializer(feast).data

        self.assertIsNone(data["name"])

    def test_icon_serialization_needs_no_queries_when_icon_selected(self):
        """Queryset precondition: the nested icon comes from the cached
        relation. With ``select_related("icon")`` satisfied, serializing
        any number of feasts costs zero additional queries.
        """
        icon = Icon.objects.create(
            title="Shared",
            church=self.church,
            image=_png_upload("shared.png"),
        )
        Icon.objects.filter(pk=icon.pk).update(cached_thumbnail_url="https://cdn.example.com/shared-thumb.jpg")
        for index in range(3):
            Feast.objects.create(church=self.church, name=f"Feast {index}", icon=icon)

        feasts = list(Feast.objects.select_related("icon").order_by("id"))

        with self.assertNumQueries(0):
            data = [FeastPublicSerializer(feast).data for feast in feasts]

        self.assertEqual(len(data), 3)
        for payload in data:
            self.assertEqual(payload["icon"]["id"], icon.id)
            self.assertEqual(payload["icon"]["title"], "Shared")
            self.assertEqual(
                payload["icon"]["thumbnail_url"],
                "https://cdn.example.com/shared-thumb.jpg",
            )


# ---------------------------------------------------------------------------
# Fast (media + annotated dates + learn_more_url)
# ---------------------------------------------------------------------------


class FastPublicSerializerContractTests(TestCase):
    """Public Fast serializer: media, annotated dates, no UI date state."""

    def setUp(self):
        self.church = Church.objects.create(name="Fast Church")

    def _make_fast(self, **kwargs):
        defaults = dict(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
            culmination_feast="Easter",
            culmination_feast_date=datetime.date(2026, 4, 12),
            year=2026,
            url="https://example.com/lent",
        )
        defaults.update(kwargs)
        # ``Fast.save()`` clears ``cached_thumbnail_url`` when no image is
        # uploaded, so the supplied value cannot survive ``create()``.
        # Pin a deterministic cached URL via queryset update after the
        # post-save hook has run, mirroring the pattern used by the
        # Icon serializer tests.
        cached_thumbnail_url = kwargs.get(
            "cached_thumbnail_url",
            "https://cdn.example.com/lent-thumb.jpg",
        )
        fast = Fast.objects.create(**defaults)
        Fast.objects.filter(pk=fast.pk).update(cached_thumbnail_url=cached_thumbnail_url)
        fast.refresh_from_db()
        return fast

    def test_returns_exact_field_set(self):
        fast = self._make_fast()

        data = FastPublicSerializer(fast).data

        self.assertEqual(
            set(data.keys()),
            {
                "id",
                "church_id",
                "name",
                "description",
                "start_date",
                "end_date",
                "culmination_feast",
                "culmination_feast_date",
                "year",
                "image_url",
                "thumbnail_url",
                "learn_more_url",
            },
        )

    def test_field_types_and_basic_values(self):
        fast = self._make_fast()

        data = FastPublicSerializer(fast).data

        self.assertIsInstance(data["id"], int)
        self.assertEqual(data["church_id"], self.church.id)
        self.assertIsInstance(data["church_id"], int)
        self.assertEqual(data["name"], "Lent")
        self.assertEqual(data["description"], "Forty-day fast.")
        self.assertEqual(data["culmination_feast"], "Easter")
        self.assertEqual(data["culmination_feast_date"], "2026-04-12")
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["learn_more_url"], "https://example.com/lent")
        self.assertEqual(data["thumbnail_url"], "https://cdn.example.com/lent-thumb.jpg")
        self.assertIsNone(data["image_url"])

    def test_empty_description_serializes_as_null(self):
        fast = self._make_fast(description="")

        data = FastPublicSerializer(fast).data

        self.assertIsNone(data["description"])

    def test_empty_culmination_feast_serializes_as_null(self):
        fast = self._make_fast(culmination_feast="")

        data = FastPublicSerializer(fast).data

        self.assertIsNone(data["culmination_feast"])

    def test_empty_name_serializes_as_null(self):
        fast = self._make_fast(name="")

        data = FastPublicSerializer(fast).data

        self.assertIsNone(data["name"])

    def test_dates_are_null_when_unannotated(self):
        fast = self._make_fast()

        data = FastPublicSerializer(fast).data

        self.assertIsNone(data["start_date"])
        self.assertIsNone(data["end_date"])

    def test_dates_consume_pre_annotated_values_only(self):
        """Sentinel: the serializer reads annotations verbatim and issues
        no queries of its own during serialization. Wrapping only the
        ``.data`` access in ``assertNumQueries(0)`` isolates precisely the
        serialization call.
        """
        fast = self._make_fast()
        # Pre-annotate as the public view would.
        fast.start_date = datetime.date(2026, 2, 15)
        fast.end_date = datetime.date(2026, 4, 4)

        with self.assertNumQueries(0):
            data = FastPublicSerializer(fast).data

        self.assertEqual(data["start_date"], "2026-02-15")
        self.assertEqual(data["end_date"], "2026-04-04")

    def test_with_dates_manager_satisfies_the_date_precondition(self):
        """``Fast.objects.with_dates()`` produces exactly the annotations
        the serializer consumes, and serialization adds no queries on top.
        """
        fast = self._make_fast()
        Day.objects.create(date=datetime.date(2026, 2, 15), fast=fast, church=self.church)
        Day.objects.create(date=datetime.date(2026, 2, 20), fast=fast, church=self.church)
        Day.objects.create(date=datetime.date(2026, 4, 4), fast=fast, church=self.church)

        annotated = Fast.objects.with_dates().get(pk=fast.pk)

        with self.assertNumQueries(0):
            data = FastPublicSerializer(annotated).data

        self.assertEqual(data["start_date"], "2026-02-15")
        self.assertEqual(data["end_date"], "2026-04-04")

    def test_learn_more_url_maps_from_fast_url(self):
        fast = self._make_fast(url="https://learn.example.org/x")
        data = FastPublicSerializer(fast).data
        self.assertEqual(data["learn_more_url"], "https://learn.example.org/x")

    def test_learn_more_url_is_null_when_unset(self):
        fast = self._make_fast(url=None)
        data = FastPublicSerializer(fast).data
        self.assertIsNone(data["learn_more_url"])

    def test_thumbnail_url_is_null_when_no_cache(self):
        fast = self._make_fast()
        Fast.objects.filter(pk=fast.pk).update(cached_thumbnail_url=None)
        fast.refresh_from_db()
        self.assertFalse(fast.cached_thumbnail_url)

        data = FastPublicSerializer(fast).data

        self.assertIsNone(data["thumbnail_url"])

    def test_thumbnail_url_does_not_access_imagespecfield_url(self):
        """Sentinel: must not invoke ``Fast.image_thumbnail.url``.

        Accessing the ImageSpecField URL triggers thumbnail generation
        and S3 I/O. Public serialization must rely solely on the cached
        column.
        """
        fast = self._make_fast()

        def _explode(_):
            raise AssertionError("Public Fast serializer must not read ImageSpecField.url")

        with patch.object(type(fast), "image_thumbnail", new=property(_explode)):
            data = FastPublicSerializer(fast).data

        self.assertEqual(data["thumbnail_url"], "https://cdn.example.com/lent-thumb.jpg")

    def test_thumbnail_url_does_not_trigger_generation_or_model_writes(self):
        """Sentinel: serialization must not generate thumbnails or save.

        We replace ``cached_thumbnail_url`` with None, then patch
        ``Fast.image_thumbnail`` (the ImageSpecField descriptor) to raise
        if the serializer ever reads it. We also patch ``Fast.save`` to
        raise if called during serialization.
        """

        class _Forbidden(Exception):
            pass

        fast = self._make_fast()
        Fast.objects.filter(pk=fast.pk).update(cached_thumbnail_url=None)
        fast.refresh_from_db()

        def _spec_explode(_self):
            raise _Forbidden("ImageSpecField accessed during public serialization")

        def _save_explode(*_a, **_kw):
            raise _Forbidden("Fast.save called during serialization")

        with (
            patch.object(Fast, "save", side_effect=_save_explode),
            patch.object(Fast, "image_thumbnail", new=property(_spec_explode)),
        ):
            data = FastPublicSerializer(fast).data

        self.assertIsNone(data["thumbnail_url"])
        self.assertIsNone(data["image_url"])  # no image uploaded

    def test_image_url_is_null_when_no_image_uploaded(self):
        fast = self._make_fast()

        data = FastPublicSerializer(fast).data

        self.assertIsNone(data["image_url"])

    def test_image_url_is_set_when_image_uploaded(self):
        fast = self._make_fast(image=_png_upload("lent.png"))

        data = FastPublicSerializer(fast).data

        self.assertIsInstance(data["image_url"], str)
        # Local FileSystemStorage in tests resolves to /test_media/.
        self.assertTrue(data["image_url"].endswith("lent.png"))

    def test_excludes_modal_and_ui_date_state(self):
        fast = self._make_fast()

        data = FastPublicSerializer(fast).data

        for excluded in (
            "modal_id",
            "countdown",
            "joined",
            "participant_count",
            "days_to_feast",
            "has_passed",
            "next_fast_date",
            "total_number_of_days",
            "current_day_number",
            "culmination_feast_salutation",
            "culmination_feast_message",
            "culmination_feast_message_attribution",
            "has_day_zero",
            "image",
            "image_thumbnail",
            "cached_thumbnail_url",
            "cached_thumbnail_updated",
            "church",
            "i18n",
        ):
            self.assertNotIn(excluded, data)


# ---------------------------------------------------------------------------
# Localization (Fast + Reading + Feast)
# ---------------------------------------------------------------------------


class PublicSerializerLocalizationTests(TestCase):
    """Localization: requested language then canonical/base-language fallback."""

    def setUp(self):
        self.church = Church.objects.create(name="Loc Church")

    def test_fast_falls_back_to_canonical_when_no_translation_registered(self):
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
        )
        # No Armenian translation is registered; expected to fall back to
        # the canonical English name.
        data = FastPublicSerializer(fast, context={"lang": "hy"}).data
        self.assertEqual(data["name"], "Lent")
        self.assertEqual(data["description"], "Forty-day fast.")

    def test_reading_falls_back_to_canonical_when_no_translation_registered(self):
        day = Day.objects.create(date=datetime.date(2026, 3, 1), church=self.church)
        reading = Reading.objects.create(
            day=day,
            book="Genesis",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
        )
        data = ReadingPublicSerializer(reading, context={"lang": "hy"}).data
        self.assertEqual(data["book"], "Genesis")

    def test_feast_falls_back_to_canonical_when_no_translation_registered(self):
        feast = Feast.objects.create(church=self.church, name="Easter")
        data = FeastPublicSerializer(feast, context={"lang": "hy"}).data
        self.assertEqual(data["name"], "Easter")

    def test_fast_returns_armenian_name_when_translation_registered(self):
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
            culmination_feast="Easter",
            culmination_feast_date=datetime.date(2026, 4, 12),
            year=2026,
        )
        fast.name_hy = "Մեծ Պահք"
        fast.save(update_fields=["i18n"])

        data = FastPublicSerializer(fast, context={"lang": "hy"}).data
        self.assertEqual(data["name"], "Մեծ Պահք")

    def test_fast_returns_armenian_description_when_translation_registered(self):
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
            culmination_feast="Easter",
            culmination_feast_date=datetime.date(2026, 4, 12),
            year=2026,
        )
        fast.description_hy = "Քառասունօրյա պահք՝ Զատիկից առաջ:"
        fast.save(update_fields=["i18n"])

        data = FastPublicSerializer(fast, context={"lang": "hy"}).data
        self.assertEqual(data["description"], "Քառասունօրյա պահք՝ Զատիկից առաջ:")

    def test_fast_returns_armenian_culmination_feast_when_translation_registered(self):
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
            culmination_feast="Easter",
            culmination_feast_date=datetime.date(2026, 4, 12),
            year=2026,
        )
        fast.culmination_feast_hy = "Զատիկ"
        fast.save(update_fields=["i18n"])

        data = FastPublicSerializer(fast, context={"lang": "hy"}).data
        self.assertEqual(data["culmination_feast"], "Զատիկ")

    def test_reading_returns_armenian_book_when_translation_registered(self):
        day = Day.objects.create(date=datetime.date(2026, 3, 1), church=self.church)
        reading = Reading.objects.create(
            day=day,
            book="Genesis",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
        )
        reading.book_hy = "Ծննդոց"
        reading.save(update_fields=["i18n"])

        data = ReadingPublicSerializer(reading, context={"lang": "hy"}).data
        self.assertEqual(data["book"], "Ծննդոց")

    def test_feast_returns_armenian_name_when_translation_registered(self):
        feast = Feast.objects.create(church=self.church, name="Easter")
        feast.name_hy = "Զատիկ"
        feast.save(update_fields=["i18n"])

        data = FeastPublicSerializer(feast, context={"lang": "hy"}).data
        self.assertEqual(data["name"], "Զատիկ")

    def test_fast_uses_active_request_language_when_context_lacks_lang(self):
        """No ``lang`` in context: active request language (set via
        ``translation.override``) wins over the ``en`` default.

        Contract: the override is scoped to the ``with`` block, so once it
        exits the active language must be restored to whatever it was on
        entry. This guards against the prior regression in which bare
        ``activate(lang)`` inside ``_localized`` permanently mutated worker
        language state and caused later serializers/tests to see a stale
        language.
        """
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
            culmination_feast="Easter",
            culmination_feast_date=datetime.date(2026, 4, 12),
            year=2026,
        )
        fast.name_hy = "Մեծ Պահք"
        fast.save(update_fields=["i18n"])

        before = translation.get_language()
        with translation.override("hy"):
            data = FastPublicSerializer(fast).data
        self.assertEqual(data["name"], "Մեծ Պահք")
        self.assertEqual(translation.get_language(), before)

    def test_context_lang_takes_precedence_over_request_query_params(self):
        """Regression pin: serializers must never read the request.

        Views validate ``?lang`` and pass the result as ``context['lang']``.
        If the serializer ever re-read the raw query param, a normalization
        change in the view would desynchronize the response cache key from
        the localized body.
        """
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
        )
        fast.name_hy = "Մեծ Պահք"
        fast.save(update_fields=["i18n"])
        request = SimpleNamespace(query_params={"lang": "en"})

        data = FastPublicSerializer(fast, context={"request": request, "lang": "hy"}).data

        self.assertEqual(data["name"], "Մեծ Պահք")

    def test_unknown_context_lang_falls_back_to_canonical(self):
        """A language outside the registered set yields the canonical
        value, never an error or an empty string.
        """
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
        )

        data = FastPublicSerializer(fast, context={"lang": "fr"}).data

        self.assertEqual(data["name"], "Lent")

    def test_translation_failure_logs_warning_and_falls_back_to_canonical(self):
        """If the translation override or accessor raises, the failure is
        logged and the canonical value is served.
        """
        fast = Fast.objects.create(
            church=self.church,
            name="Lent",
            description="Forty-day fast.",
        )

        with patch(
            "bahk.public_api.v1.serializers.override",
            side_effect=RuntimeError("override exploded"),
        ):
            with self.assertLogs("bahk.public_api.v1.serializers", level="WARNING") as logs:
                data = FastPublicSerializer(fast, context={"lang": "hy"}).data

        self.assertEqual(data["name"], "Lent")
        self.assertTrue(any("translation lookup failed" in message for message in logs.output))


class ReadOnlySerializerContractTests(TestCase):
    """Every public serializer is read-only by construction."""

    def test_save_refused_instead_of_persisting_an_empty_row(self):
        """Reviewer reproduction: with every field read-only, DRF
        validates any payload vacuously, so ``save()`` must refuse rather
        than persist an empty model row.
        """
        count_before = Church.objects.count()
        serializer = ChurchPublicSerializer(data={"name": "Injected"})

        self.assertTrue(serializer.is_valid())
        with self.assertRaises(NotImplementedError):
            serializer.save()

        self.assertEqual(Church.objects.count(), count_before)
        self.assertFalse(Church.objects.filter(name__in=("", "Injected")).exists())

    def test_create_and_update_refused_for_every_public_serializer(self):
        for serializer_cls in (
            ChurchPublicSerializer,
            IconPublicSerializer,
            ReadingPublicSerializer,
            FeastPublicSerializer,
            FastPublicSerializer,
        ):
            with self.subTest(serializer=serializer_cls.__name__):
                with self.assertRaises(NotImplementedError):
                    serializer_cls().create({})
                with self.assertRaises(NotImplementedError):
                    serializer_cls().update(None, {})


# ---------------------------------------------------------------------------
# Media helpers (unit-level sanity checks)
# ---------------------------------------------------------------------------


class MediaHelperTests(TestCase):
    """Direct unit checks for the helper functions used by the serializers."""

    def test_image_url_returns_none_when_no_image(self):
        church = Church.objects.create(name="Helper Church")
        fast = Fast.objects.create(church=church, name="No Image Fast")
        self.assertIsNone(_image_url(fast, "image"))

    def test_cached_thumbnail_url_returns_none_when_unset(self):
        church = Church.objects.create(name="Helper Church 2")
        fast = Fast.objects.create(church=church, name="No Thumbnail Fast")
        Fast.objects.filter(pk=fast.pk).update(cached_thumbnail_url=None)
        fast.refresh_from_db()
        self.assertIsNone(_cached_thumbnail_url(fast))

    def test_image_url_logs_warning_and_degrades_when_storage_raises(self):
        church = Church.objects.create(name="Helper Church 3")
        fast = Fast.objects.create(
            church=church,
            name="Broken Storage Fast",
            image=_png_upload("broken.png"),
        )

        class _Boom(Exception):
            pass

        def _raise(_self):
            raise _Boom("storage exploded")

        with patch.object(type(fast.image), "url", new=property(_raise)):
            with self.assertLogs("bahk.public_api.v1.serializers", level="WARNING") as logs:
                data = FastPublicSerializer(fast).data

        self.assertIsNone(data["image_url"])
        self.assertTrue(any("resolving image URL failed for Fast" in message for message in logs.output))
