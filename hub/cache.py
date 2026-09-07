"""Cache helpers for hub API responses."""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# The shape of the feast API response body, folded into its cache key. Bump this in the SAME
# commit as any change to that body's keys.
#
# The generation below invalidates content; this invalidates shape, and the two need to be
# separate because shape has to survive a rollback. Bumping the generation by hand on deploy
# would orphan the old entries once, but a revert would then read the new-shaped entries back out
# of the cache and serve them verbatim -- the old code returns a cached body without inspecting
# it. Keyed on a constant that travels with the code, each version only ever reads entries it
# wrote, in both directions, with nothing to remember at deploy time.
#
#   1: {"date", "feast"}
#   2: {"date", "feasts", "feast"}  -- "feast" deprecated, see hub/views/feasts.py
FEAST_API_RESPONSE_SHAPE = 2


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
    """Return the public feast API cache key.

    Carries both axes of staleness: ``FEAST_API_RESPONSE_SHAPE`` for the body's shape, which
    changes with the code, and the per-church generation for its content, which changes at runtime.
    """
    return (
        f"feast:s{FEAST_API_RESPONSE_SHAPE}:{date_obj}:{church_id}:{lang}"
        f":{feast_api_generation(church_id)}"
    )


def invalidate_feast_api_cache_for_church(church_id):
    """Invalidate every feast API entry for a church.

    A feast is a commemoration served on many dates, so there is no single entry to drop. Bumping
    the church's generation orphans them all in one operation -- see :func:`feast_api_generation`.

    Over-invalidating a church is deliberate and cheap: feast enrichment changes are rare (an
    admin action, or an LLM context finishing) and the entries rebuild from one engine call.

    Takes the id rather than a row, so a caller that has just merged rows away -- the backfill in
    migration 0066 -- has something valid to pass.
    """
    key = _feast_generation_key(church_id)
    try:
        try:
            cache.incr(key)
        except ValueError:
            # incr requires the key to exist; if it has expired, any generation is fresh enough.
            cache.set(key, 1, None)
    except Exception:
        logger.warning(
            "Failed to invalidate feast API cache for church %s", church_id, exc_info=True)


def invalidate_feast_api_cache_for_feast(feast):
    """Invalidate every feast API entry for this feast's church."""
    invalidate_feast_api_cache_for_church(feast.church_id)
