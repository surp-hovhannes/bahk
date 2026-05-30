"""Utilities for importing prayer sets from JSON."""

from django.db import transaction
from django.utils.translation import gettext as _

from prayers.models import Prayer, PrayerSet, PrayerSetMembership

VALID_CATEGORIES = {"morning", "evening", "general"}
SET_REQUIRED_FIELDS = ("title", "category", "prayers")
PRAYER_REQUIRED_FIELDS = ("title", "text", "category")
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

    for set_index, prayer_set in enumerate(prayer_sets, start=1):
        if not isinstance(prayer_set, dict):
            raise ValueError(_("Prayer set %(index)s must be an object.") % {"index": set_index})

        _validate_required_fields(prayer_set, SET_REQUIRED_FIELDS, f"Prayer set {set_index}")
        _validate_category(prayer_set["category"], f"Prayer set {set_index}")

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
            _validate_category(prayer["category"], context)


def detect_conflicts(data: dict, church) -> list[dict]:
    """Detect existing prayer sets and prayers with matching titles for a church."""
    prayer_sets = data.get("prayer_sets", [])
    set_titles = [prayer_set["title"] for prayer_set in prayer_sets]
    prayer_titles = [prayer["title"] for prayer_set in prayer_sets for prayer in prayer_set.get("prayers", [])]

    conflicts = []
    for prayer_set in PrayerSet.objects.filter(church=church, title__in=set_titles).order_by("title", "id"):
        conflicts.append(
            {
                "type": "Prayer Set",
                "title": prayer_set.title,
                "existing_id": prayer_set.id,
            }
        )

    for prayer in Prayer.objects.filter(church=church, title__in=prayer_titles).order_by("title", "id"):
        conflicts.append(
            {
                "type": "Prayer",
                "title": prayer.title,
                "existing_id": prayer.id,
            }
        )

    return conflicts


def execute_import(data: dict, church) -> tuple[int, int]:
    """Create prayer sets, prayers, and ordered memberships from import data."""
    sets_created = 0
    prayers_created = 0

    with transaction.atomic():
        for set_data in data["prayer_sets"]:
            prayers = []
            for prayer_data in set_data["prayers"]:
                prayer = Prayer.objects.create(
                    title=prayer_data["title"],
                    text=prayer_data["text"],
                    category=prayer_data["category"],
                    church=church,
                )
                _apply_translations(prayer, prayer_data, "prayer")
                _apply_tags(prayer, prayer_data.get("tags", []))
                prayer.save()
                prayers.append(prayer)
                prayers_created += 1

            prayer_set = PrayerSet.objects.create(
                title=set_data["title"],
                description=set_data.get("description", ""),
                category=set_data["category"],
                church=church,
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

    return sets_created, prayers_created


def get_prayer_titles(data: dict) -> list[str]:
    """Return prayer titles from import data in JSON order."""
    return [prayer["title"] for prayer_set in data.get("prayer_sets", []) for prayer in prayer_set.get("prayers", [])]


def get_import_counts(data: dict) -> dict:
    """Return preview counts for import data."""
    prayer_sets = data.get("prayer_sets", [])
    return {
        "sets": len(prayer_sets),
        "prayers": sum(len(prayer_set.get("prayers", [])) for prayer_set in prayer_sets),
    }


def _validate_required_fields(item: dict, required_fields: tuple[str, ...], context: str) -> None:
    for field in required_fields:
        if field not in item or item[field] in (None, ""):
            raise ValueError(
                _('%(context)s is missing required field "%(field)s".')
                % {
                    "context": context,
                    "field": field,
                }
            )


def _validate_category(category: str, context: str) -> None:
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
    for field in TRANSLATABLE_FIELDS[object_type]:
        for key, value in item.items():
            prefix = f"{field}_"
            if key.startswith(prefix) and value:
                setattr(obj, key, value)

    translations = item.get("translations") or item.get("i18n") or {}
    if not isinstance(translations, dict):
        return

    for language_code, translated_fields in translations.items():
        if not isinstance(translated_fields, dict):
            continue
        for field in TRANSLATABLE_FIELDS[object_type]:
            value = translated_fields.get(field)
            if value:
                setattr(obj, f"{field}_{language_code}", value)
