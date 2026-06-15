# Feast API Cache Invalidation Plan

## Context

`GET /api/feasts/?date=2026-06-15` served a stale cached response with `icon: null` after icon matching had already set `Feast.icon_id=627` for Feast 282.

The cache entry is written in `hub/views/feasts.py` as:

```python
cache_key = f"feast:{date_obj}:{church.id}:{lang}"
cache.set(cache_key, response_data, 3600)
```

The response includes feast fields, serialized icon data, active context text, short text, and context vote counters, so changes to either `Feast` or `FeastContext` need to invalidate the relevant date/church/language entries.

## Scope

- Invalidate cached `/api/feasts/` responses when feast data changes.
- Invalidate cached `/api/feasts/` responses when feast context data changes.
- Preserve the existing `feast:{date}:{church_id}:{lang}` cache key shape and 3600 second TTL.
- Do not redesign context generation, icon matching, scraping, or feast name normalization.
- Do not change frontend behavior.

## Feature Branch

Create a branch from the current base:

```bash
git checkout -b fix/feast-api-cache-invalidation
```

Keep the branch limited to the files below plus tests.

## Implementation

### 1. Centralize feast API cache helpers

Add a small helper module, preferably `hub/cache.py`.

Responsibilities:

- Build the existing feast API cache key from `date`, `church_id`, and `lang`.
- Delete one exact cache key.
- Delete all known language variants for a date/church pair.
- Use `cache.delete_pattern("feast:{date}:{church_id}:*")` when available, because production Redis via `django-redis` should support it.
- Fall back to exact `cache.delete_many(...)` for test/local backends that do not support pattern deletion.

Suggested public API:

```python
def feast_api_cache_key(date_obj, church_id, lang):
    return f"feast:{date_obj}:{church_id}:{lang}"

def feast_api_cache_languages():
    # Include settings.MODELTRANS_AVAILABLE_LANGUAGES, settings.LANGUAGES,
    # settings.LANGUAGE_CODE, and "en"; return a de-duplicated list.

def invalidate_feast_api_cache_for_date(date_obj, church_id):
    # Best effort. Delete pattern first when supported, then exact known keys.

def invalidate_feast_api_cache_for_feast(feast):
    # Uses feast.day.date and feast.day.church_id.
```

Implementation notes:

- Import `django.core.cache.cache` inside this helper, not in multiple callers.
- Avoid raising from invalidation. Log warnings if the cache backend delete fails.
- Do not include a separate `bahk:` prefix; Django cache settings already handle any backend key prefixing.
- Keep language fallback broad enough to cover no-query public requests (`en`) and explicit `lang=hy`.

### 2. Use the helper in the feast view

Update `hub/views/feasts.py`:

- Replace the inline `cache_key = f"feast:{date_obj}:{church.id}:{lang}"` with `feast_api_cache_key(date_obj, church.id, lang)`.
- Keep the existing cache lookup/set behavior and TTL unchanged.
- Import only the helper needed by the view.

This prevents future key drift between the view and invalidation logic.

### 3. Invalidate on Feast saves/deletes

Update `hub/signals.py`:

- Extend the existing `post_save` receiver for `Feast` so every saved feast invalidates its date/church feast API cache.
- Keep the existing creation-only designation and icon matching tasks unchanged.
- Add a `post_delete` receiver for `Feast` that invalidates the same date/church keys.

Why this fixes the observed bug:

- `hub/tasks/icon_tasks.py` assigns an icon with `feast.save(update_fields=['icon'])`.
- That emits `post_save`.
- The signal invalidates `feast:{date}:{church_id}:*`.
- The next `GET /api/feasts/?date=2026-06-15` recomputes and serializes the non-null icon.

Suggested shape:

```python
@receiver(post_save, sender=Feast)
def handle_feast_save(sender, instance, created, **kwargs):
    invalidate_feast_api_cache_for_feast(instance)
    ...

@receiver(post_delete, sender=Feast)
def handle_feast_delete(sender, instance, **kwargs):
    invalidate_feast_api_cache_for_feast(instance)
```

### 4. Invalidate on FeastContext saves/deletes

Update `hub/signals.py`:

- Import `FeastContext`.
- Add `post_save` and `post_delete` receivers for `FeastContext`.
- Invalidate via `instance.feast`.

This covers context creation and updates from `hub/tasks/llm_tasks.py`, because `_create_feast_context_with_translations(...)` and `_update_feast_context_translations(...)` both call `context.save()`.

### 5. Invalidate after feedback counter updates

Update `hub/views/feasts.py` in `FeastContextFeedbackView.post`.

Reason:

- The feedback endpoint increments `thumbs_up` and `thumbs_down` via `FeastContext.objects.filter(...).update(...)`.
- `QuerySet.update()` bypasses model `save()` and Django signals.
- The cached `/api/feasts/` response includes `context_thumbs_up` and `context_thumbs_down`.

After each successful atomic update, call `invalidate_feast_api_cache_for_feast(feast)` before returning.

Keep this scoped to the two successful feedback branches. Do not change regeneration behavior.

### 6. Add regression tests

Add focused tests to `hub/tests/test_feast_views.py`, likely in `FeastViewCacheTests`, or create `hub/tests/test_feast_cache_invalidation.py` if the class gets too large.

Recommended tests:

1. `test_feast_save_invalidates_cached_icon_response`
   - Create a `Day`, `Feast` without icon, and `Icon`.
   - Request `/api/feasts/?date=...` once and assert cached `icon is None`.
   - Set `feast.icon = icon` and `feast.save(update_fields=['icon'])`.
   - Request the same URL again.
   - Assert `icon` is not null and has the expected icon id.

2. `test_icon_matching_save_invalidates_cached_response`
   - Same setup, but call `match_icon_to_feast_task(feast.id)` with `_match_icons_with_llm` mocked to return the icon with `confidence: "high"`.
   - First request should cache `icon: null`.
   - Task should save the icon.
   - Second request should show the icon.

3. `test_feast_context_save_invalidates_cached_context_response`
   - Create a feast and active context with `text="Old"`, `short_text="Old short"`.
   - Request once and assert old context text.
   - Modify the context fields and call `context.save()`.
   - Request again and assert new context text.

4. `test_feast_context_feedback_invalidates_cached_vote_counts`
   - Create a feast with active context.
   - Request `/api/feasts/` once to cache `context_thumbs_up: 0`.
   - POST `{"feedback_type": "up"}` to `/api/feasts/<id>/feedback/`.
   - Request `/api/feasts/` again and assert `context_thumbs_up: 1`.

5. `test_feast_delete_invalidates_cached_response`
   - Cache a populated feast response.
   - Delete the feast.
   - Request again and assert the endpoint no longer returns the deleted feast from cache.

Test details:

- Use `cache.clear()` in setup.
- Patch `hub.views.feasts.generate_feast_context_task.delay`, `hub.signals.match_icon_to_feast_task.delay`, and `hub.signals.determine_feast_designation_task.delay` where fixture creation would otherwise enqueue work.
- Use the existing `tests.test_settings` locmem cache. The fallback exact-key deletion path must make these tests pass without Redis pattern deletion.
- Keep tests out of slow/performance tags unless explicitly needed.

## Targeted Verification

Run focused tests locally:

```bash
python manage.py test hub.tests.test_feast_views --settings=tests.test_settings
python manage.py test hub.tests.test_feast_icon_matching --exclude-tag=slow --settings=tests.test_settings
```

If a new test file is created, run it directly:

```bash
python manage.py test hub.tests.test_feast_cache_invalidation --settings=tests.test_settings
```

## Full / Crabbox Verification

Run the project test job through Crabbox:

```bash
scripts/crabbox-validate.sh test
```

For final review confidence, run CI:

```bash
scripts/crabbox-validate.sh ci
```

The Crabbox `test` and `ci` jobs already run Django tests with:

```bash
python manage.py test --noinput --parallel --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings
```

## Review Checklist

- Confirm no cache keys are renamed, only centralized.
- Confirm `Feast.icon` changes via `save(update_fields=['icon'])` invalidate cached `/api/feasts/` responses.
- Confirm `FeastContext.save()` invalidates context text and short text in cached responses.
- Confirm feedback `QuerySet.update()` paths explicitly invalidate vote counters.
- Confirm invalidation is best-effort and cannot break feast saves, deletes, context generation, or feedback submission if Redis is unavailable.
- Confirm no unrelated context generation, scraping, normalization, or icon matching behavior changed.
