"""Utilities for importing prayer sets from JSON."""

import logging

from django.conf import settings as django_settings
from django.db import transaction
from django.utils.translation import gettext as _

from hub.models import Church
from prayers.models import Prayer, PrayerSet, PrayerSetMembership

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"morning", "evening", "general"}
SET_REQUIRED_FIELDS = ("title", "category", "prayers")
PRAYER_REQUIRED_FIELDS = ("title", "text", "category")
PRAYER_SET_TITLE_MAX_LENGTH = 128
PRAYER_TITLE_MAX_LENGTH = 200
TRANSLATABLE_FIELDS = {
    "set": ("title", "description"),
    "prayer": ("title", "text"),
}


def validate_import_json(data: dict) -> None:
    """Validate the prayer set import payload."""
    if not isinstance(data, dict):
        raise ValueError(_("Import JSON must be an object."))

    prayer_sets = data.get("prayer_sets")
    if not isinstance(prayer_sets, list) or not prayer_sets:
        raise ValueError(_("Import JSON must include a non-empty prayer_sets array."))

    set_titles_seen: dict[str, str] = {}
    prayer_titles_seen: dict[str, str] = {}

    for set_index, prayer_set in enumerate(prayer_sets, start=1):
        if not isinstance(prayer_set, dict):
            raise ValueError(_("Prayer set %(index)s must be an object.") % {"index": set_index})

        set_context = f"Prayer set {set_index}"
        _validate_required_fields(prayer_set, SET_REQUIRED_FIELDS, set_context)
        _validate_string_field(
            prayer_set["title"],
            "title",
            set_context,
            max_length=PRAYER_SET_TITLE_MAX_LENGTH,
        )
        if "description" in prayer_set and prayer_set["description"] not in (None, ""):
            _validate_string_field(prayer_set["description"], "description", set_context)
        _validate_category(prayer_set["category"], set_context)

        set_title_key = prayer_set["title"].casefold()
        if set_title_key in set_titles_seen:
            raise ValueError(
                _('Duplicate prayer set title "%(title)s" (titles are matched case-insensitively).')
                % {"title": prayer_set["title"]}
            )
        set_titles_seen[set_title_key] = prayer_set["title"]

        prayers = prayer_set["prayers"]
        if not isinstance(prayers, list) or not prayers:
            raise ValueError(
                _('Prayer set "%(title)s" must include a non-empty prayers array.')
                % {"title": prayer_set.get("title", set_index)}
            )

        for prayer_index, prayer in enumerate(prayers, start=1):
            if not isinstance(prayer, dict):
                raise ValueError(
                    _('Prayer %(prayer_index)s in set "%(title)s" must be an object.')
                    % {"prayer_index": prayer_index, "title": prayer_set.get("title", set_index)}
                )

            context = f'Prayer {prayer_index} in set "{prayer_set["title"]}"'
            _validate_required_fields(prayer, PRAYER_REQUIRED_FIELDS, context)
            _validate_string_field(
                prayer["title"],
                "title",
                context,
                max_length=PRAYER_TITLE_MAX_LENGTH,
            )
            _validate_string_field(prayer["text"], "text", context)
            _validate_category(prayer["category"], context)
            prayer_title_key = prayer["title"].casefold()
            if prayer_title_key in prayer_titles_seen:
                raise ValueError(
                    _('Duplicate prayer title "%(title)s" (titles are matched case-insensitively).')
                    % {"title": prayer["title"]}
                )
            prayer_titles_seen[prayer_title_key] = prayer["title"]
            if "tags" in prayer:
                _validate_tags(prayer["tags"], context)


def detect_conflicts(data: dict, church) -> list[dict]:
    """Detect existing prayer sets and prayers with matching titles for a church.

    Matching is case-insensitive so 'Morning Set' and 'morning set' are
    treated as duplicates.
    """
    prayer_sets = data.get("prayer_sets", [])
    set_title_keys = {prayer_set["title"].casefold() for prayer_set in prayer_sets}
    prayer_title_keys = {
        prayer["title"].casefold()
        for prayer_set in prayer_sets
        for prayer in prayer_set.get("prayers", [])
    }

    conflicts = []
    if set_title_keys:
        for prayer_set in PrayerSet.objects.filter(church=church).order_by("title", "id"):
            if prayer_set.title.casefold() in set_title_keys:
                conflicts.append(
                    {
                        "type": "Prayer Set",
                        "title": prayer_set.title,
                        "existing_id": prayer_set.id,
                    }
                )

    if prayer_title_keys:
        for prayer in Prayer.objects.filter(church=church).order_by("title", "id"):
            if prayer.title.casefold() in prayer_title_keys:
                conflicts.append(
                    {
                        "type": "Prayer",
                        "title": prayer.title,
                        "existing_id": prayer.id,
                    }
                )

    return conflicts


def execute_import(data: dict, church) -> tuple[int, int, list[int]]:
    """Create prayer sets, prayers, and ordered memberships from import data.

    Returns a tuple of (sets_created, prayers_created, created_prayer_ids).
    """
    sets_created = 0
    prayers_created = 0
    created_prayer_ids: list[int] = []

    with transaction.atomic():
        locked_church = Church.objects.select_for_update().get(pk=church.pk)
        conflicts = detect_conflicts(data, locked_church)
        if conflicts:
            raise ValueError(_("Import blocked because title conflicts were detected."))

        for set_data in data["prayer_sets"]:
            prayers: list[Prayer] = []
            for prayer_data in set_data["prayers"]:
                prayer = Prayer(
                    title=prayer_data["title"],
                    text=prayer_data["text"],
                    category=prayer_data["category"],
                    church=locked_church,
                )
                _apply_translations(prayer, prayer_data, "prayer")
                prayer.save()
                _apply_tags(prayer, prayer_data.get("tags", []))
                prayers.append(prayer)
                created_prayer_ids.append(prayer.id)
                prayers_created += 1

            prayer_set = PrayerSet(
                title=set_data["title"],
                description=set_data.get("description", ""),
                category=set_data["category"],
                church=locked_church,
            )
            _apply_translations(prayer_set, set_data, "set")
            prayer_set.save()

            PrayerSetMembership.objects.bulk_create(
                [
                    PrayerSetMembership(
                        prayer_set=prayer_set,
                        prayer=prayer,
                        order=order,
                    )
                    for order, prayer in enumerate(prayers, start=1)
                ]
            )
            sets_created += 1

    return sets_created, prayers_created, created_prayer_ids


def get_import_counts(data: dict) -> dict:
    """Return preview counts for import data."""
    prayer_sets = data.get("prayer_sets", [])
    return {
        "sets": len(prayer_sets),
        "prayers": sum(len(prayer_set.get("prayers", [])) for prayer_set in prayer_sets),
    }


def _validate_required_fields(item: dict, required_fields: tuple[str, ...], context: str) -> None:
    """Raise ValueError if any required field is missing or empty."""
    for field in required_fields:
        if field not in item or item[field] in (None, ""):
            raise ValueError(
                _('%(context)s is missing required field "%(field)s".')
                % {
                    "context": context,
                    "field": field,
                }
            )


def _validate_string_field(
    value,
    field_name: str,
    context: str,
    *,
    max_length: int | None = None,
) -> None:
    """Raise ValueError if a field is not a non-empty string or exceeds max length."""
    if not isinstance(value, str):
        raise ValueError(
            _('%(context)s field "%(field)s" must be a string.') % {"context": context, "field": field_name}
        )
    if not value.strip():
        raise ValueError(
            _('%(context)s field "%(field)s" must not be empty.') % {"context": context, "field": field_name}
        )
    if max_length is not None and len(value) > max_length:
        raise ValueError(
            _('%(context)s field "%(field)s" exceeds maximum length of %(max)s characters.')
            % {"context": context, "field": field_name, "max": max_length}
        )


def _validate_tags(tags, context: str) -> None:
    """Raise ValueError if tags are not a string, list of strings, or empty."""
    if tags in (None, "", []):
        return
    if isinstance(tags, str):
        return
    if isinstance(tags, list):
        for tag_index, tag in enumerate(tags, start=1):
            if not isinstance(tag, str):
                raise ValueError(
                    _("%(context)s tag %(index)s must be a string.") % {"context": context, "index": tag_index}
                )
        return
    raise ValueError(_('%(context)s field "tags" must be a string or array of strings.') % {"context": context})


def _validate_category(category: str, context: str) -> None:
    if not isinstance(category, str):
        raise ValueError(_('%(context)s field "category" must be a string.') % {"context": context})
    if category not in VALID_CATEGORIES:
        valid_categories = ", ".join(sorted(VALID_CATEGORIES))
        raise ValueError(
            _('%(context)s has invalid category "%(category)s". Valid categories: %(valid)s.')
            % {"context": context, "category": category, "valid": valid_categories}
        )


def _apply_tags(obj, tag_names) -> None:
    if not tag_names:
        return

    if isinstance(tag_names, str):
        tag_names = [tag.strip() for tag in tag_names.split(",")]

    clean_tag_names = [str(tag).strip() for tag in tag_names if str(tag).strip()]
    if clean_tag_names:
        obj.tags.add(*clean_tag_names)


def _apply_translations(obj, item: dict, object_type: str) -> None:
    known_language_codes = {lang for lang, _ in getattr(django_settings, "LANGUAGES", [])}

    for field in TRANSLATABLE_FIELDS[object_type]:
        for key, value in item.items():
            prefix = f"{field}_"
            if key.startswith(prefix) and value:
                suffix = key[len(prefix) :]
                if suffix in known_language_codes:
                    setattr(obj, key, value)
                else:
                    logger.warning(
                        "Skipping unrecognized translation key '%s' for %s (unknown language code)",
                        key,
                        object_type,
                    )

    translations = item.get("translations") or item.get("i18n") or {}
    if not isinstance(translations, dict):
        return

    for language_code, translated_fields in translations.items():
        if not isinstance(translated_fields, dict):
            continue
        if language_code not in known_language_codes:
            logger.warning(
                "Skipping translations for unknown language code '%s' in %s",
                language_code,
                object_type,
            )
            continue
        for field in TRANSLATABLE_FIELDS[object_type]:
            value = translated_fields.get(field)
            if value:
                setattr(obj, f"{field}_{language_code}", value)
