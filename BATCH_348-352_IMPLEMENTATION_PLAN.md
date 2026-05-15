# Implementation Plan: Sentry Batch #348–#352

## ⚙️ Mandatory Dispatch Workflow

Every fix MUST follow this exact sequence before the PR is created:

1. **Branch** off main: `fix/issue-NNN-short-description`
2. **Implement** the fix
3. **Run lint:** `ruff check .` — fix any failures until clean
4. **Run tests** (if applicable): `python manage.py test --exclude-tag=slow --exclude-tag=performance --settings=tests.test_settings`
5. **Confirm lint still clean** after any test-driven edits
6. **Create PR** to main
7. **Spawn reviewer sub-agent** to review the PR
8. **Wait for CI** — do not admin-merge unless CI is green (or the failure is pre-existing and unrelated)
9. **Merge** after CI passes and review is approved

---

## Executive Summary

This plan covers 5 Sentry issues affecting the BAHK backend. After codebase analysis, the root causes are:

1. **#348** — `str.format()` crashes on prayer request titles containing curly braces, causing the cron to fail every 15 minutes.
2. **#349** — `/api/feasts/` makes synchronous, unprotected HTTP calls to `sacredtradition.am` in the request path; worker timeouts manifest as `SystemExit` kills.
3. **#350** — `sacredtradition.am` scrapers lack URL validation, retry logic, timeouts, and graceful fallbacks.
4. **#351** — Book names from the scraper may use curly/smart quotes (e.g., `'` U+2019) that don't match the straight-apostrophe keys in `CATENA_ABBREV_FOR_BOOK`.
5. **#352** — `FastListView` caches raw querysets (anti-pattern), and `FastSerializer` fallbacks hit `obj.days` per instance (N+1).

---

## Priority Order

| Priority | Issue | Rationale |
|----------|-------|-----------|
| **P0** | #348 | Highest event volume (2,422), trivial 3-line fix, zero risk |
| **P1** | #349 + #350 | Coupled: endpoint crashes because scraping is brittle. High user impact (162 users). |
| **P2** | #351 | Easy win (book-name normalization), fixes broken Catena links for 65 users |
| **P3** | #352 | Performance optimization; lowest per-user event rate (~1.3×/user) |

---

## Issue #348 - Fix cron: `check-expired-prayer-requests-frequent`

### Root Cause
```python
# prayers/tasks.py:481
message=PRAYER_REQUEST_COMPLETED_MESSAGE.format(title=prayer_request.title),
```
If `title` contains literal `{` or `}` (e.g. `"Pray for {my} family"`), `str.format()` raises `KeyError`, crashing the entire batch and causing the task to retry/fail every 15 minutes.

### Files to Modify
- `prayers/tasks.py` - safe-format the message + wrap loop body in `try/except`
- `notifications/tasks.py` - audit & fix all `.format(fast_name=...)` / `.format(title=...)` calls with user-provided strings
- `notifications/constants.py` - add comment warning about format-string safety
- `tests/test_prayer_requests.py` - add regression test with curly braces in title

### Approach
1. **Replace `.format()` with `.replace('{title}', title, 1)`** in `prayers/tasks.py` (and equivalent patterns in `notifications/tasks.py`).
2. **Wrap each iteration** of the `expired_requests` loop in its own `try/except` so one bad request never crashes the batch.
3. **Add `select_for_update(nowait=True)` fallback** - if a row is already locked by a concurrent worker, skip it and let the next cron run handle it.

### Estimate
- **Lines changed:** ~15
- **Risk:** Very low - only changes error handling and string formatting
- **Time:** 30 min

### Testing
- Unit test: create a prayer request with title `"Test {brace} title"`, set it expired+approved, run the task → expect no crash, request marked completed.
- Unit test: verify `nowait=True` path when row is locked.

---

## Issues #349 & #350 - Feast endpoint crash + sacredtradition.am scraping failures

### Root Cause
- `GetFeastForDate.get()` calls `get_or_create_feast_for_date()` → `scrape_feast()` → **synchronous HTTP call** in the web request path.
- `scrape_feast()` / `scrape_readings()` use `urllib.request.urlopen` with no retry, no circuit breaker, inconsistent timeouts, and no stale/cached fallback.
- Worker timeouts during slow scrapes cause the process supervisor to kill the worker (reported as `SystemExit` in Sentry).
- The view also calls `generate_feast_context_task.delay(feast.id)` unconditionally when context is missing - this is fine, but the preceding scrape is not.

### Files to Modify
- `hub/utils.py` - refactor scrapers:
  - Add URL validation (`urllib.parse.urlparse`)
  - Add `requests` session with retries (or `urllib` retry wrapper)
  - Add consistent 10s timeout + User-Agent header
  - Add `functools.lru_cache` or Redis cache for feast scrape results (TTL 6h)
  - Add circuit-breaker: if 3 consecutive scrape failures, return `None` for 15 min
- `hub/views/feasts.py` - `GetFeastForDate.get()`:
  - Wrap **entire** feast-building logic in `try/except Exception`
  - On any error during scrape/serialization, return a degraded response (feast object without context, or HTTP 503 with cached data)
  - Add Django cache around `get_or_create_feast_for_date` call (cache key: `feast:{date}:{church_id}`)
- `hub/serializers.py` - `FeastPrayerSerializer.to_representation()`:
  - Wrap `instance.render_for_feast()` in `try/except` to prevent one bad template from crashing the whole response
- `tests/test_feast_views.py` or `hub/tests/test_feast_views.py` - add tests for degraded response

### Approach
1. **Layer 1 (prevention):** Cache feast scrape results for 6h so the external site is not hit on every request.
2. **Layer 2 (resilience):** Add circuit breaker + retry logic to `scrape_feast` and `scrape_readings`.
3. **Layer 3 (safety):** Wrap the view's serialization pipeline in broad exception handling; return partial data instead of crashing.
4. **Layer 4 (monitoring):** Change `logging.error` to `sentry_sdk.capture_exception()` for scrape failures so they still alert but don't crash users.

### Estimate
- **Lines changed:** ~100-130
- **Risk:** Medium - touches the critical feasts endpoint; requires careful testing of degraded modes
- **Time:** 3-4 hours

### Testing
- Mock `urllib.request.urlopen` to raise `URLError` → assert 200 OK with degraded response, no `SystemExit`.
- Mock 10s delay → assert worker does not crash (or use `timeout` mock).
- Verify cache hit: second request for same date does not call scraper.
- Regression test: `FeastPrayerSerializer` with malformed `feast.name` still returns 200.

### Dependency
- **#350 is a prerequisite sub-task of #349.** Fix scraper robustness first, then add the circuit breaker in the view.

---

## Issue #351 - Fix missing Catena URL mapping for Hebrews

### Root Cause
`CATENA_ABBREV_FOR_BOOK` keys use straight ASCII apostrophe `'` (U+0027). The `sacredtradition.am` scraper may return book names with curly/smart quotes (`'` U+2019 or `"` U+201C), causing `CATENA_ABBREV_FOR_BOOK.get(self.book)` to return `None`. Sentry reports this for "St. Paul's Epistle to the Hebrews" and potentially other Pauline epistles.

### Files to Modify
- `hub/constants.py` - add a `normalize_book_name()` helper; update `CATENA_ABBREV_FOR_BOOK` to use **both** straight and curly-quote variants (or do lookup via normalized form).
- `hub/models.py` - `Reading.create_url()`:
  - Replace direct `.get(self.book)` with `normalize_book_name(self.book)` before lookup
  - On miss, log the *normalized* name for easier future mapping
- `hub/tests/test_bible_api.py` - add test ensuring every key survives normalization round-trip

### Approach
```python
# hub/constants.py
import unicodedata

def normalize_book_name(name: str | None) -> str | None:
    if not name:
        return None
    # NFKC + replace curly quotes with straight quotes + strip
    name = unicodedata.normalize("NFKC", name)
    name = name.replace("\u2018", "'").replace("\u2019", "'")
    name = name.replace("\u201c", '"').replace("\u201d", '"')
    return name.strip()

# Build a normalized lookup dict
CATENA_ABBREV_FOR_BOOK_NORMALIZED = {
    normalize_book_name(k): v
    for k, v in CATENA_ABBREV_FOR_BOOK.items()
}
```

### Estimate
- **Lines changed:** ~30
- **Risk:** Very low - additive lookup logic
- **Time:** 45 min

### Testing
- Unit test: `normalize_book_name("St. Paul's Epistle to the Hebrews")` and `normalize_book_name("St. Paul's Epistle to the Hebrews")` both resolve to `"heb"`.
- Unit test: `Reading.create_url()` with curly-quote book name produces correct Catena URL.

---

## Issue #352 - Fix slow DB query on `/api/fasts/`

### Root Cause
1. `FastListView.get_queryset()` caches the **raw queryset** (`cache.set(cache_key, queryset, timeout=600)`). Caching a Django queryset is an anti-pattern; it pickles the query and may cause stale/missing prefetches.
2. `FastSerializer` has several `SerializerMethodField`s with fallbacks that query `obj.days` per instance:
   - `get_next_fast_date` → `obj.days.filter(...).first()`
   - `get_total_number_of_days` → `obj.days.count()`
   - `get_current_day_number` → `obj.days.filter(...).exists()` + `.count()`
   These fallbacks fire whenever the view's annotations are absent (e.g. cache miss, or the view is reused elsewhere).
3. The view does `.annotate(participant_count=Count('profiles', distinct=True))` but never `prefetch_related('profiles')`, and the serializer accesses `obj.profiles.count()` in fallback.

### Files to Modify
- `hub/views/fast.py` - `FastListView`:
  - **Stop caching the queryset object**; instead cache the serialized output (or skip queryset-level caching entirely and rely on DB + prefetch).
  - Add `prefetch_related('days', 'profiles')` to the queryset.
  - Ensure all serializer-required annotations are present in the queryset so fallbacks are never hit.
- `hub/serializers.py` - `FastSerializer`:
  - Remove N+1 fallback branches (or replace with `.only()` / `.prefetch_related` friendly lookups).
  - Use the annotated fields exclusively; assert they exist.
- `hub/models.py` - verify `Day` indexes are hit; add `models.Index(fields=['church', 'date'])` if missing (already present, but confirm).
- `tests/integration/test_endpoints.py` or `hub/tests/test_fast_views.py` - add `assertNumQueries` test for `FastListView`.

### Approach
1. **Remove queryset caching** - serialize and cache the final JSON payload, or remove caching and add `prefetch_related`.
2. **Add `prefetch_related('days', 'profiles')`** to the queryset.
3. **Inline the missing annotations** in the view that the serializer currently falls back to:
   - `next_fast_date` - subquery or annotation on `days__date__gte=today`
   - `end_date`, `start_date` - already annotated, good
4. **Audit `ThumbnailCacheMixin`** - `update_thumbnail_cache` calls `obj.save()` during serialization, causing writes on GET requests. Move thumbnail cache warming to a background task or `post_save` signal.

### Estimate
- **Lines changed:** ~50
- **Risk:** Medium - query changes can subtly change result counts; needs `assertNumQueries` verification
- **Time:** 2-3 hours

### Testing
- `django.test.utils.override_settings(CACHES=...)`: assert `/api/fasts/` completes in ≤ 5 queries regardless of fast count.
- Regression test: verify paginated / non-paginated results are identical before/after.

---

## Cross-Cutting Recommendations

1. **Sentry Cron Alert Tuning**
   - The `check-expired-prayer-requests-frequent` cron fires every 15 min but has no `max_runtime` configured in Sentry. Add `checkin_margin=5` and `max_runtime=10` to the monitor config so Sentry distinguishes "task took too long" from "task crashed".

2. **Shared Scraper Infrastructure**
   - #349 and #350 both touch `hub/utils.py`. Extract a small `_fetch_sacredtradition(url)` helper with retries, timeout, and circuit breaker so all scrapers benefit.

3. **String `.format()` Audit**
   - Search the entire repo for `.format(name=`, `.format(title=`, `.format(fast_name=` and replace with `.replace('{...}', value)` whenever the template value is user-provided. This prevents the class of bug seen in #348 from recurring.

---

## Appendix: File Inventory

| File | Lines (approx) | Issue(s) |
|------|----------------|---------|
| `prayers/tasks.py` | ~15 | #348 |
| `notifications/tasks.py` | ~10 | #348 (audit similar patterns) |
| `notifications/constants.py` | +3 comments | #348 |
| `hub/utils.py` | ~80 | #349, #350, #351 |
| `hub/views/feasts.py` | ~30 | #349 |
| `hub/serializers.py` | ~15 | #349, #352 |
| `hub/constants.py` | ~20 | #351 |
| `hub/models.py` | ~5 | #351 |
| `hub/views/fast.py` | ~40 | #352 |
| `hub/mixins.py` | ~10 | #352 (thumbnail write-on-read) |
| Various `tests/` | ~50 | All |

**Total estimated lines changed:** ~250-300
**Total estimated review + QA time:** 6-8 hours
