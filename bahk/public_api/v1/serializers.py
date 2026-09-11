"""Presentation-neutral serializers for the versioned public v1 API.

These serializers shape a stable, contract-tested subset of the Fast & Pray
data model for public consumers. They are intentionally decoupled from the
internal hub/icons serializers: their job is field selection, naming, and
localization only, not querying or computation.

Invariants enforced here (also asserted in contract tests):

* Read-only, presentation-neutral. Every serializer derives from
  ``PublicReadOnlySerializer``, whose ``create()`` and ``update()`` refuse
  unconditionally, so a public serializer can never persist model rows
  even when handed valid-looking payload data. No writable fields, no
  nested create/update semantics, no model writes on read.
* Thumbnail URLs are read from the cached column only. Accessing the
  ``ImageSpecField.url`` would trigger generation, which performs S3 I/O and
  is intentionally forbidden in the public read path. When no cached URL is
  present the field serializes to JSON ``null``.
* Original image URLs come from ``obj.image.url`` only when an image has been
  uploaded; otherwise the field serializes to JSON ``null``. Storage failures
  degrade to ``null`` and are logged, never silent.
* Fast date fields (``start_date`` / ``end_date``) are consumed exclusively
  from pre-annotated attributes; build querysets with
  ``Fast.objects.with_dates()`` so the precondition holds by construction.
  Serializers do not query related ``Day`` rows; when no annotation is
  present the field serializes to ``null``.
* The nested Feast ``icon`` is read from the instance's cached relation:
  querysets feeding ``FeastPublicSerializer`` must
  ``select_related("icon")``, or serialization costs one extra query per
  feast.
* User-facing text fields follow the language resolved by the view and
  passed as ``context['lang']``; serializers never read the request. When
  absent, the Django-resolved active language is used, then ``en``. Missing
  translations fall back to the canonical/base language field, and missing
  or empty values serialize as JSON ``null`` — never the empty string. The
  fallback uses the ``{field}_i18n`` accessor added by
  ``django-modeltrans``; this is the same pattern used by the internal
  serializers, but re-implemented here so the public contract does not
  depend on a private serializer.
* Excluded fields per resource: Fast ``modal_id``, ``countdown``, ``joined``,
  counts, derived UI date state, culmination salutation/message/attribution,
  cache fields. Feast designation/context/votes/LLM/prayer fields. Icon
  tags/hashes/timestamps/church/admin data. Reading text/context/fetch/legacy
  fields.
"""

import logging

from django.utils.translation import get_language, override
from rest_framework import serializers

from hub.models import Church, Fast, Feast, Reading
from icons.models import Icon

logger = logging.getLogger(__name__)


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

    Storage failures degrade to ``None`` so a misconfigured backend cannot
    break public responses, but they are logged so the degraded output is
    diagnosable instead of silently null across every response.
    """
    image = getattr(instance, image_field_name, None)
    if not image:
        return None
    try:
        url = image.url
    except Exception:
        logger.warning(
            "Public v1: resolving %s URL failed for %s pk=%s; serializing null",
            image_field_name,
            type(instance).__name__,
            instance.pk,
        )
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

    Mounted public views validate ``?lang`` eagerly and hand the validated
    value to the serializer as ``context['lang']``; that value is the single
    source of truth here, so the language that localized the body is always
    the language that keyed the response cache. When ``context['lang']`` is
    absent (direct serializer use outside the mounted views), fall back to
    the Django-resolved active language, then ``en`` to match the rest of
    the API surface. The request is never read: resolving the query string
    is the view's job.
    """
    context = context or {}
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
    translation: missing or empty values serialize as ``None``. If the
    override or accessor raises (for example when ``lang`` is not a known
    language code), the failure is logged and the canonical base field is
    used instead of serving a stale prior language.
    """
    lang = _resolve_lang(context)
    translated = None
    try:
        with override(lang):
            translated = getattr(instance, f"{field_name}_i18n", None)
    except Exception:
        logger.warning(
            "Public v1: %s.%s translation lookup failed (lang=%r); falling back to the canonical value",
            type(instance).__name__,
            field_name,
            lang,
        )
        translated = None
    if translated:
        return translated
    return getattr(instance, field_name, None) or None


# ---------------------------------------------------------------------------
# Read-only base
# ---------------------------------------------------------------------------


class PublicReadOnlySerializer(serializers.ModelSerializer):
    """Base class for the public v1 serializers: read-only by construction.

    ``create()`` and ``update()`` refuse unconditionally. With every field
    read-only, DRF validates any payload vacuously and a ``save()`` would
    otherwise persist empty model rows, so the read-only invariant is
    enforced here rather than left to convention. ``localized()`` is the
    one home for the shared language wiring used by user-facing fields.
    """

    def create(self, validated_data):
        raise NotImplementedError("Public v1 serializers are read-only; create() is not supported.")

    def update(self, instance, validated_data):
        raise NotImplementedError("Public v1 serializers are read-only; update() is not supported.")

    def localized(self, instance, field_name):
        """Return ``field_name`` on ``instance`` for ``context['lang']``."""
        return _localized(instance, field_name, self.context)


# ---------------------------------------------------------------------------
# Church
# ---------------------------------------------------------------------------


class ChurchPublicSerializer(PublicReadOnlySerializer):
    """Public read-only Church serializer.

    Contract: ``id`` (int), ``name`` (string, canonical/base language; church
    names do not currently have translations).
    """

    class Meta:
        model = Church
        fields = ["id", "name"]
        read_only_fields = list(fields)


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------


class IconPublicSerializer(PublicReadOnlySerializer):
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
        read_only_fields = list(fields)

    def get_image_url(self, obj):
        return _image_url(obj, "image")

    def get_thumbnail_url(self, obj):
        return _cached_thumbnail_url(obj)


# ---------------------------------------------------------------------------
# Reading (citation only)
# ---------------------------------------------------------------------------


class ReadingPublicSerializer(PublicReadOnlySerializer):
    """Public read-only Reading serializer (citation only).

    Contract fields:
        * ``id`` (int)
        * ``sequence`` (int|null): ordering within the day's readings
        * ``book`` (string): canonical or translated book name; ``null``
          when empty
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
        read_only_fields = list(fields)

    def get_book(self, obj):
        return self.localized(obj, "book")


# ---------------------------------------------------------------------------
# Feast
# ---------------------------------------------------------------------------


class FeastPublicSerializer(PublicReadOnlySerializer):
    """Public read-only Feast serializer.

    Contract fields:
        * ``id`` (int)
        * ``name`` (string): localized name with canonical fallback;
          ``null`` when empty
        * ``icon`` (object|null): nested IconPublicSerializer or ``None``
          when no icon is matched

    Queryset precondition: the nested ``icon`` is read from the instance's
    cached relation, so querysets feeding this serializer must use
    ``select_related("icon")``. An unselected relation costs one extra
    query per serialized feast; the contract tests pin the zero-query
    contract with ``assertNumQueries(0)``.

    Excluded: ``church``, ``church_id``, ``designation``, context/votes/LLM/
    prayer fields, and any other internal metadata.
    """

    icon = IconPublicSerializer(read_only=True)
    name = serializers.SerializerMethodField()

    class Meta:
        model = Feast
        fields = ["id", "name", "icon"]
        read_only_fields = list(fields)

    def get_name(self, obj):
        return self.localized(obj, "name")


# ---------------------------------------------------------------------------
# Fast
# ---------------------------------------------------------------------------


class FastPublicSerializer(PublicReadOnlySerializer):
    """Public read-only Fast serializer.

    Contract fields:
        * ``id`` (int)
        * ``church_id`` (int): owning church
        * ``name`` (string): localized name; ``null`` when empty
        * ``description`` (string|null): localized description
        * ``start_date`` (date|null): pre-annotated only — build the
          queryset with ``Fast.objects.with_dates()``; ``null`` when absent
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
        read_only_fields = list(fields)

    def get_name(self, obj):
        return self.localized(obj, "name")

    def get_description(self, obj):
        return self.localized(obj, "description")

    def get_culmination_feast(self, obj):
        return self.localized(obj, "culmination_feast")

    def get_image_url(self, obj):
        return _image_url(obj, "image")

    def get_thumbnail_url(self, obj):
        return _cached_thumbnail_url(obj)

    def get_learn_more_url(self, obj):
        url = getattr(obj, "url", None)
        return url or None
