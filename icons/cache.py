"""View-level cache helpers for icon API responses."""

import hashlib
import json
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class IconViewCache:
    """Centralized cache keys, TTLs, and invalidation for icon API views."""

    LIST_TTL = 60 * 60        # 1 hour
    DETAIL_TTL = 6 * 60 * 60  # 6 hours
    MATCH_TTL = 5 * 60        # 5 minutes

    LIST_PREFIX = "icons:list"
    DETAIL_PREFIX = "icons:detail"
    MATCH_PREFIX = "icons:match"
    VERSION_KEY = "icons:view-cache-version"

    @classmethod
    def _digest(cls, value):
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _version(cls):
        version = cache.get(cls.VERSION_KEY)
        if version is None:
            cache.add(cls.VERSION_KEY, 1, timeout=None)
            version = cache.get(cls.VERSION_KEY, 1)
        return version

    @classmethod
    def _versioned_key(cls, prefix, suffix):
        return f"{prefix}:v{cls._version()}:{suffix}"

    @classmethod
    def list_key(cls, query_params):
        normalized = {
            key: query_params.getlist(key)
            for key in sorted(query_params.keys())
        }
        return cls._versioned_key(cls.LIST_PREFIX, cls._digest(normalized))

    @classmethod
    def detail_key(cls, icon_id):
        return cls._versioned_key(cls.DETAIL_PREFIX, icon_id)

    @classmethod
    def match_key(cls, data):
        prompt = data.get("prompt") or ""
        normalized = {
            "prompt": str(prompt).strip(),
            "church_id": data.get("church_id") or "",
            "return_format": data.get("return_format", "full"),
            "max_results": data.get("max_results", 3),
        }
        return cls._versioned_key(cls.MATCH_PREFIX, cls._digest(normalized))

    @classmethod
    def clear_all(cls):
        """Invalidate all icon view cache entries.

        Redis supports pattern deletion. The version bump also invalidates
        entries on simpler test backends such as locmem.
        """
        deleted = 0
        try:
            cache.add(cls.VERSION_KEY, 1, timeout=None)
            try:
                cache.incr(cls.VERSION_KEY)
            except ValueError:
                cache.set(cls.VERSION_KEY, int(cache.get(cls.VERSION_KEY, 1)) + 1, timeout=None)
        except Exception as exc:
            logger.warning("Icon cache version invalidation failed: %s", exc)

        patterns = [
            f"{cls.LIST_PREFIX}:*",
            f"{cls.DETAIL_PREFIX}:*",
            f"{cls.MATCH_PREFIX}:*",
        ]
        for pattern in patterns:
            try:
                if hasattr(cache, "delete_pattern"):
                    deleted += int(cache.delete_pattern(pattern) or 0)
            except Exception as exc:
                logger.warning(
                    "Icon cache invalidation failed for %s: %s",
                    pattern,
                    exc,
                )
        return deleted
