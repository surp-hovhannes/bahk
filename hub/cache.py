"""Cache helpers for hub API responses."""

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def feast_api_generation(church_id):
    """Return the current cache generation for a church's feast API entries.

    Feasts are keyed by commemoration, not by date, so a feast has no single date whose cache
    entry could be deleted -- it is served on every day the engine names it, which for something
    like "Fast day" is thousands of days. Rather than enumerate those, the generation is folded
    into the cache key and invalidation just bumps it, which orphans every entry for the church
    at once. Old entries become unreachable and age out on their existing TTL.

    That keeps invalidation O(1) and correct on every cache backend, rather than depending on a
    pattern-delete the locmem backend does not have.
    """
    try:
        return cache.get_or_set(_feast_generation_key(church_id), 1, None) or 1
    except Exception:
        # A cache that cannot be read cannot be serving stale entries either.
        logger.warning("Failed to read feast cache generation for church %s", church_id,
                       exc_info=True)
        return 1


def _feast_generation_key(church_id):
    return f"feast-generation:{church_id}"


def feast_api_cache_key(date_obj, church_id, lang):
    """Return the public feast API cache key."""
    return f"feast:{date_obj}:{church_id}:{lang}:{feast_api_generation(church_id)}"


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
    """Invalidate every feast API entry for this feast's church.

    A feast is a commemoration served on many dates, so there is no single entry to drop. Bumping
    the church's generation orphans them all in one operation -- see :func:`feast_api_generation`.

    Over-invalidating a church is deliberate and cheap: feast enrichment changes are rare (an
    admin action, or an LLM context finishing) and the entries rebuild from one engine call.
    """
    key = _feast_generation_key(feast.church_id)
    try:
        try:
            cache.incr(key)
        except ValueError:
            # incr requires the key to exist; if it has expired, any generation is fresh enough.
            cache.set(key, 1, None)
    except Exception:
        logger.warning("Failed to invalidate feast API cache for feast %s", feast.pk, exc_info=True)
