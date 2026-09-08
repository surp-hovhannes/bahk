"""Presentation-neutral serializers for the versioned public v1 API.

These serializers shape a stable, contract-tested subset of the Fast & Pray
data model for public consumers. They are intentionally decoupled from the
internal hub/icons serializers: their job is field selection, naming, and
localization only, not querying or computation.

Invariants enforced here (also asserted in contract tests):

* Read-only, presentation-neutral. No writable fields, no nested create/update
  semantics, no model writes on read.
* Thumbnail URLs are read from the cached column only. Accessing the
  ``ImageSpecField.url`` would trigger generation, which performs S3 I/O and
  is intentionally forbidden in the public read path. When no cached URL is
  present the field serializes to JSON ``null``.
* Original image URLs come from ``obj.image.url`` only when an image has been
  uploaded; otherwise the field serializes to JSON ``null``.
* Fast date fields (``start_date`` / ``end_date``) are consumed exclusively
  from pre-annotated attributes. Serializers do not query related ``Day``
  rows; when no annotation is present the field serializes to ``null``.
* User-facing text fields follow the requested language (``context['lang']``
  query string) and fall back to the canonical/base language field when no
  translation is registered. The fallback uses the ``{field}_i18n`` accessor
  added by ``django-modeltrans``; this is the same pattern used by the
  internal serializers, but re-implemented here so the public contract does
  not depend on a private serializer.
* Excluded fields per resource: Fast ``modal_id``, ``countdown``, ``joined``,
  counts, derived UI date state, culmination salutation/message/attribution,
  cache fields. Feast designation/context/votes/LLM/prayer fields. Icon
  tags/hashes/timestamps/church/admin data. Reading text/context/fetch/legacy
  fields.
"""

from django.utils.translation import get_language, override
from rest_framework import serializers

from hub.models import Church, Fast, Feast, Reading
from icons.models import Icon


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------


def _image_url(instance, image_field_name):
    """Return the URL of an uploaded image, or ``None``.

    ``ImageField.url`` resolves the storage backend's URL. In production that
    is S3 and is cheap; in tests the local FileSystemStorage also resolves
    cheaply. Crucially this MUST NOT touch the corresponding
    ``ImageSpecField`` (``thumbnail`` / ``image_thumbnail``), because doing
    so would trigger thumbnail generation and S3 I/O during serialization.
    """
    image = getattr(instance, image_field_name, None)
    if not image:
        return None
    try:
        url = image.url
    except Exception:
        return None
    return url or None


def _cached_thumbnail_url(instance):
    """Return the pre-cached thumbnail URL, or ``None``.

    The cached URL is populated by ``Model.save()`` after upload and never
    requires a thumbnail to be regenerated. Accessing
    ``instance.thumbnail.url`` or ``instance.image_thumbnail.url`` is
    explicitly avoided so this serializer cannot trigger generation, S3 I/O,
    cache writes, or model writes.
    """
    cached = getattr(instance, "cached_thumbnail_url", None)
    return cached or None


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------


def _resolve_lang(context):
    """Resolve the requested language from serializer context.

    Public consumers can pass ``?lang=hy`` on the request. When absent we
    honor an explicit ``context['lang']`` (used by tests and helpers), then
    fall back to the Django-resolved active language, then ``en`` to match
    the rest of the API surface.
    """
    context = context or {}
    request = context.get("request")
    if request is not None:
        query_params = getattr(request, "query_params", None)
        if query_params is not None:
            lang = query_params.get("lang")
            if lang:
                return lang
    explicit = context.get("lang")
    if explicit:
        return explicit
    active = get_language()
    if active:
        return active
    return "en"


def _localized(instance, field_name, context):
    """Return the translated value for ``field_name`` with canonical fallback.

    Activates the requested language via a scoped ``translation.override``
    context manager while reading the ``{field}_i18n`` accessor provided
    by ``django-modeltrans``. The override is reverted on context exit, so
    subsequent serializer calls and tests never observe leaked language
    state from prior invocations. Falls back to the canonical
    (base-language) attribute when the accessor yields no value, so the
    serializer never emits an empty string in place of a missing
    translation. If the override or accessor raises (for example when
    ``lang`` is not a known language code), ``translated`` is left as
    ``None`` and the canonical base field is used instead of serving a
    stale prior language.
    """
    lang = _resolve_lang(context)
    translated = None
    try:
        with override(lang):
            translated = getattr(instance, f"{field_name}_i18n", None)
    except Exception:
        translated = None
    if translated:
        return translated
    return getattr(instance, field_name, None)


# ---------------------------------------------------------------------------
# Church
# ---------------------------------------------------------------------------


class ChurchPublicSerializer(serializers.ModelSerializer):
    """Public read-only Church serializer.

    Contract: ``id`` (int), ``name`` (string, canonical/base language; church
    names do not currently have translations).
    """

    class Meta:
        model = Church
        fields = ["id", "name"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------


class IconPublicSerializer(serializers.ModelSerializer):
    """Public read-only Icon serializer.

    Contract fields:
        * ``id`` (int)
        * ``title`` (string, canonical/base language; ``Icon`` has no
          translated fields registered)
        * ``image_url`` (string|null): URL of the original uploaded image
          only when one is present
        * ``thumbnail_url`` (string|null): cached thumbnail URL only. Must
          not access ``ImageSpecField.url`` or trigger generation.

    Excluded (non-exhaustive): ``church``, ``church_id``, ``tags``,
    ``tag_list``, ``image_hash``, ``phash``, ``image_content_digest``,
    ``image_revision``, ``original_filename``, ``filename_provenance``,
    ``created_at``, ``updated_at``, and any cache columns.
    """

    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Icon
        fields = ["id", "title", "image_url", "thumbnail_url"]
        read_only_fields = fields

    def get_image_url(self, obj):
        return _image_url(obj, "image")

    def get_thumbnail_url(self, obj):
        return _cached_thumbnail_url(obj)


# ---------------------------------------------------------------------------
# Reading (citation only)
# ---------------------------------------------------------------------------


class ReadingPublicSerializer(serializers.ModelSerializer):
    """Public read-only Reading serializer (citation only).

    Contract fields:
        * ``id`` (int)
        * ``sequence`` (int|null): ordering within the day's readings
        * ``book`` (string): canonical or translated book name
        * ``start_chapter`` (int)
        * ``start_verse`` (int)
        * ``end_chapter`` (int)
        * ``end_verse`` (int)

    Excluded: legacy ``text*`` fields, ``text_copyright``, ``text_version``,
    ``text_fetched_at``, ``fums_token``, ``passage_key``, ``day`` and any
    AI-generated context/thumbs or passage text content.
    """

    book = serializers.SerializerMethodField()

    class Meta:
        model = Reading
        fields = [
            "id",
            "sequence",
            "book",
            "start_chapter",
            "start_verse",
            "end_chapter",
            "end_verse",
        ]
        read_only_fields = fields

    def get_book(self, obj):
        return _localized(obj, "book", self.context)


# ---------------------------------------------------------------------------
# Feast
# ---------------------------------------------------------------------------


class FeastPublicSerializer(serializers.ModelSerializer):
    """Public read-only Feast serializer.

    Contract fields:
        * ``id`` (int)
        * ``name`` (string): localized name with canonical fallback
        * ``icon`` (object|null): nested IconPublicSerializer or ``None``
          when no icon is matched

    Excluded: ``church``, ``church_id``, ``designation``, context/votes/LLM/
    prayer fields, and any other internal metadata.
    """

    icon = IconPublicSerializer(read_only=True)

    class Meta:
        model = Feast
        fields = ["id", "name", "icon"]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["name"] = _localized(instance, "name", self.context)
        return data


# ---------------------------------------------------------------------------
# Fast
# ---------------------------------------------------------------------------


class FastPublicSerializer(serializers.ModelSerializer):
    """Public read-only Fast serializer.

    Contract fields:
        * ``id`` (int)
        * ``church_id`` (int): owning church
        * ``name`` (string): localized name
        * ``description`` (string|null): localized description
        * ``start_date`` (date|null): pre-annotated only; ``null`` when absent
        * ``end_date`` (date|null): pre-annotated only; ``null`` when absent
        * ``culmination_feast`` (string|null): localized culmination name
        * ``culmination_feast_date`` (date|null)
        * ``year`` (int|null)
        * ``image_url`` (string|null): URL of the uploaded image only
        * ``thumbnail_url`` (string|null): cached thumbnail URL only
        * ``learn_more_url`` (string|null): mapped from ``Fast.url``

    Excluded: ``modal_id``, ``countdown``, ``joined``, ``participant_count``,
    ``days_to_feast``, ``has_passed``, ``next_fast_date``,
    ``total_number_of_days``, ``current_day_number``,
    ``culmination_feast_salutation``, ``culmination_feast_message``,
    ``culmination_feast_message_attribution``, ``cached_thumbnail_url``,
    ``cached_thumbnail_updated``, and the ``church`` nested object
    (``church_id`` is the church reference for public consumers).
    """

    church_id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    culmination_feast = serializers.SerializerMethodField()
    start_date = serializers.DateField(read_only=True, allow_null=True)
    end_date = serializers.DateField(read_only=True, allow_null=True)
    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    learn_more_url = serializers.SerializerMethodField()

    class Meta:
        model = Fast
        fields = [
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
        ]
        read_only_fields = fields

    def get_name(self, obj):
        return _localized(obj, "name", self.context)

    def get_description(self, obj):
        return _localized(obj, "description", self.context) or None

    def get_culmination_feast(self, obj):
        return _localized(obj, "culmination_feast", self.context) or None

    def get_image_url(self, obj):
        return _image_url(obj, "image")

    def get_thumbnail_url(self, obj):
        return _cached_thumbnail_url(obj)

    def get_learn_more_url(self, obj):
        url = getattr(obj, "url", None)
        return url or None
