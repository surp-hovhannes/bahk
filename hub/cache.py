"""Cache helpers for hub API responses."""

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def feast_api_cache_key(date_obj, church_id, lang):
    """Return the public feast API cache key."""
    return f"feast:{date_obj}:{church_id}:{lang}"


def feast_api_cache_languages():
    """Return likely language variants for feast API cache invalidation."""
    languages = ["en"]

    languages.extend(getattr(settings, "MODELTRANS_AVAILABLE_LANGUAGES", []))
    languages.extend(code for code, _name in getattr(settings, "LANGUAGES", []))

    language_code = getattr(settings, "LANGUAGE_CODE", None)
    if language_code:
        languages.append(language_code)

    seen = set()
    return [lang for lang in languages if lang and not (lang in seen or seen.add(lang))]


def invalidate_feast_api_cache_for_date(date_obj, church_id):
    """Best-effort invalidation for all known feast API language variants."""
    pattern = feast_api_cache_key(date_obj, church_id, "*")

    if hasattr(cache, "delete_pattern"):
        try:
            cache.delete_pattern(pattern)
        except Exception:
            logger.warning("Failed to delete feast API cache pattern %s", pattern, exc_info=True)

    keys = [
        feast_api_cache_key(date_obj, church_id, lang)
        for lang in feast_api_cache_languages()
    ]
    try:
        cache.delete_many(keys)
    except Exception:
        logger.warning("Failed to delete feast API cache keys %s", keys, exc_info=True)


def invalidate_feast_api_cache_for_feast(feast):
    """Best-effort invalidation for the API cache entries affected by a feast."""
    try:
        invalidate_feast_api_cache_for_date(feast.day.date, feast.day.church_id)
    except Exception:
        logger.warning("Failed to invalidate feast API cache for feast %s", feast.pk, exc_info=True)
