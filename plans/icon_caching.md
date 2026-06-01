# Redis View-Level Caching for Icon API Endpoints

## Context

The Render Standard web service has a 512 MB RAM limit, while the deployed Django baseline is already about 517 MB. The proximate OOM trigger is request-time churn in the icon endpoints:

- `GET /api/icons/` currently builds a queryset with `select_related('church').prefetch_related('tags')`, paginates, serializes, and returns icon data on every request.
- `POST /api/icons/match/` currently loads all candidate icons and their tags on every request before calling the LLM or fallback matcher.
- `GET /api/icons/<id>/` currently loads and serializes a single icon on every request.

There are 552 icons today. Repeatedly instantiating hundreds of model objects and prefetched tag objects is enough to keep Python memory climbing because GC does not return memory to the OS quickly enough between requests.

Redis is already configured as the default Django cache backend in `bahk/settings.py` via `django_redis.cache.RedisCache`, and thumbnail URL caching already exists at the model/DB level. This plan keeps thumbnail caching untouched and adds response-level caching only around the API views.

The Astro frontend in `bahk_landing` calls these backend endpoints. No frontend change is required; the backend should return the same JSON shape with lower repeated DB/object allocation.

## Goals

1. Cache successful responses for:
   - `GET /api/icons/` for 5 minutes.
   - `GET /api/icons/<id>/` for 15 minutes.
   - `POST /api/icons/match/` for 1 minute.
2. Vary list cache entries by query parameters, including `search`, `tags`, `church`, `page`, and `page_size`.
3. Vary match cache entries by request body fields that change output: `prompt`, `church_id`, `return_format`, and `max_results`.
4. Invalidate list, detail, and match caches when icons are created, updated, deleted, or retagged.
5. Avoid enabling global cache middleware and avoid `@cache_page` decorators because this repo notes test contamination risks from decorator/middleware caching.

## Non-Goals

- Do not change `bahk_landing`.
- Do not change the DB-level `cached_thumbnail_url` / `cached_thumbnail_updated` logic in `icons/models.py`.
- Do not redesign icon matching or reduce the LLM prompt payload in this pass.
- Do not introduce schema changes or migrations.

## Proposed Implementation

### 1. Add an icon cache helper module

Create `icons/cache.py` with a small service class that centralizes keys, TTLs, and invalidation.

Recommended shape:

```python
import hashlib
import json
import logging
from urllib.parse import urlencode

from django.core.cache import cache

logger = logging.getLogger(__name__)


class IconViewCache:
    LIST_TTL = 5 * 60
    DETAIL_TTL = 15 * 60
    MATCH_TTL = 60

    LIST_PREFIX = "icons:list"
    DETAIL_PREFIX = "icons:detail"
    MATCH_PREFIX = "icons:match"

    @classmethod
    def _digest(cls, value):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def list_key(cls, query_params):
        normalized = {
            key: query_params.getlist(key)
            for key in sorted(query_params.keys())
        }
        return f"{cls.LIST_PREFIX}:{cls._digest(normalized)}"

    @classmethod
    def detail_key(cls, icon_id):
        return f"{cls.DETAIL_PREFIX}:{icon_id}"

    @classmethod
    def match_key(cls, data):
        normalized = {
            "prompt": (data.get("prompt") or "").strip(),
            "church_id": data.get("church_id") or "",
            "return_format": data.get("return_format", "full"),
            "max_results": data.get("max_results", 3),
        }
        return f"{cls.MATCH_PREFIX}:{cls._digest(normalized)}"

    @classmethod
    def clear_all(cls):
        patterns = [
            f"{cls.LIST_PREFIX}:*",
            f"{cls.DETAIL_PREFIX}:*",
            f"{cls.MATCH_PREFIX}:*",
        ]
        deleted = 0
        for pattern in patterns:
            try:
                if hasattr(cache, "delete_pattern"):
                    deleted += int(cache.delete_pattern(pattern) or 0)
            except Exception as exc:
                logger.warning("Icon cache invalidation failed for %s: %s", pattern, exc)
        return deleted
```

Notes:

- Do not include an extra `bahk:` prefix in the logical keys; `CACHES["default"]["KEY_PREFIX"] = "bahk"` already handles the backend prefix.
- Use a stable hash instead of raw query strings so Redis keys stay short.
- Use `query_params.getlist()` so repeated query parameters produce distinct keys.
- Sorting query parameter keys makes `?church=1&search=x` and `?search=x&church=1` share the same cache entry.

### 2. Cache `GET /api/icons/`

Override `IconListView.list()` in `icons/views.py`.

Flow:

1. Only cache GET/list responses, not POST uploads.
2. Build `cache_key = IconViewCache.list_key(request.query_params)`.
3. Return `Response(cached_data)` if present.
4. On miss, call `super().list(request, *args, **kwargs)`.
5. Cache only successful `200 OK` responses using `IconViewCache.LIST_TTL`.

This preserves the current filtering, pagination, serializer, permissions, and response shape while preventing repeated model and tag object construction for hot frontend requests.

### 3. Cache `GET /api/icons/<id>/`

Override `IconDetailView.retrieve()` in `icons/views.py`.

Flow:

1. Build `cache_key = IconViewCache.detail_key(kwargs["pk"])`.
2. Return cached data when present.
3. On miss, call `super().retrieve(request, *args, **kwargs)`.
4. Cache only successful `200 OK` responses for `IconViewCache.DETAIL_TTL`.

Do not cache 404s in the first pass. Keeping misses uncached avoids stale not-found responses after a newly-created icon receives an expected ID or during unusual fixture/test flows.

### 4. Cache `POST /api/icons/match/`

Add cache lookup after basic request validation in `IconMatchView.post()`, before loading icons.

Flow:

1. Preserve current validation for missing `prompt`, invalid `return_format`, and invalid `max_results`.
2. After `max_results` is normalized to an integer, build a normalized data dict:
   - trimmed `prompt`
   - `church_id`
   - `return_format`
   - integer `max_results`
3. Look up `IconViewCache.match_key(normalized_data)`.
4. On hit, return `Response(cached_data, status=200)`.
5. On miss, run the existing matching flow.
6. Cache only successful match responses for 60 seconds.

The match TTL should stay short because match results change when new icons are uploaded, titles/tags are edited, or the LLM output changes. The explicit signal invalidation below handles local icon changes, but the short TTL limits stale AI results and keeps Redis memory bounded.

### 5. Invalidate on icon changes

Add `icons/signals.py` and register it from `IconsConfig.ready()`.

Use signals rather than extending `Icon.save()` because `Icon.save()` already owns thumbnail generation and field-update behavior. Keeping view cache invalidation separate avoids entangling response cache maintenance with thumbnail cache maintenance.

Recommended handlers:

```python
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from taggit.models import TaggedItem

from icons.cache import IconViewCache
from icons.models import Icon


@receiver(post_save, sender=Icon)
@receiver(post_delete, sender=Icon)
def invalidate_icon_view_cache(sender, **kwargs):
    IconViewCache.clear_all()


@receiver(post_save, sender=TaggedItem)
@receiver(post_delete, sender=TaggedItem)
def invalidate_icon_tag_cache(sender, instance, **kwargs):
    if instance.content_object.__class__ is Icon:
        IconViewCache.clear_all()
```

Then update `icons/apps.py`:

```python
class IconsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'icons'

    def ready(self):
        import icons.signals  # noqa: F401
```

Important details:

- `IconSerializer.create()` calls `icon.tags.set(...)` after `Icon.objects.create(...)`; invalidating on both `Icon` and `TaggedItem` changes catches uploads and later tag edits.
- If `instance.content_object` is too expensive or fragile in the `TaggedItem` handler, filter by `ContentType` for `Icon` instead.
- `clear_all()` must be best-effort and log failures. Cache invalidation failure should not break icon uploads or admin edits.

### 6. Tests

Add focused tests in `icons/tests.py` or a new `icons/tests/test_icon_caching.py`.

Recommended coverage:

- `GET /api/icons/` caches a successful list response:
  - First request executes the queryset.
  - Second identical request returns the same JSON without hitting the expensive query path.
- List cache varies by query params:
  - `/api/icons/?search=nativity`
  - `/api/icons/?search=cross`
  - `/api/icons/?tags=nativity&page=1`
- `GET /api/icons/<id>/` caches detail response and invalidates after icon update.
- `POST /api/icons/match/` caches by normalized prompt/church/return_format/max_results.
  - Mock the OpenAI client or force the existing simple fallback path to avoid real API calls.
- Signal invalidation:
  - Prime list/detail/match cache entries.
  - Save an `Icon`; assert entries are gone.
  - Change tags with `icon.tags.set(...)`; assert entries are gone.

Testing notes:

- This repo has an `AGENTS.md` warning that `@cache_page` decorators poison parallel test workers. The implementation should use explicit cache operations, and cache tests should isolate with:

```python
@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "icon-cache-tests"}})
```

- Clear the cache in `setUp()` / `tearDown()` for icon cache tests.
- For query-count assertions, remember that locmem cache does not support `delete_pattern`; test `IconViewCache.clear_all()` fallback behavior or use direct keys where needed. The production invalidation path should use `django-redis` `delete_pattern`.

### 7. Deployment and validation

Local validation:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test icons --settings=tests.test_settings
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py check
```

If running inside the project runtime rather than on the host, drop the `docker exec` prefix:

```bash
python manage.py test icons --settings=tests.test_settings
python manage.py check
```

Production validation after deploy:

1. Hit `/api/icons/` twice with the same query string and confirm the second request avoids the large icon query in logs/APM.
2. Hit `/api/icons/?search=nativity` and `/api/icons/?search=cross` and confirm separate cache entries.
3. Hit `/api/icons/match/` twice with the same payload and confirm the second call does not call OpenAI or load all icons.
4. Upload or edit an icon in admin, then confirm list/detail/match caches refresh.
5. Watch Render memory for at least one hour after deploy; expected outcome is a flatter memory curve because repeated Astro frontend traffic no longer allocates hundreds of icon/tag model objects per request.

## Implementation Order

1. Add `icons/cache.py`.
2. Add `list()` caching to `IconListView`.
3. Add `retrieve()` caching to `IconDetailView`.
4. Add validated-body caching to `IconMatchView.post()`.
5. Add `icons/signals.py` and register it in `icons/apps.py`.
6. Add focused tests for cache hits, cache-key variation, and invalidation.
7. Run icon tests and `manage.py check`.

## Risks and Mitigations

- **Stale icon data:** Signals clear caches on icon save/delete and tag changes. TTLs also bound staleness if invalidation misses an edge case.
- **Redis key growth:** Query/body keys are hashed and TTL-bound. Match uses a 60-second TTL because prompts can be high-cardinality.
- **Test contamination:** Avoid `@cache_page` and middleware caching. Use explicit `cache.get/set` and isolated test cache settings.
- **Invalidation blast radius:** Clearing all icon view caches is acceptable because there are only 552 icons and icon writes are infrequent compared with reads. This is simpler and safer than trying to surgically delete list variants.
- **Thumbnail cache coupling:** Leave `cached_thumbnail_url` untouched. Response caching stores serialized thumbnail URLs but does not generate or refresh thumbnails.
