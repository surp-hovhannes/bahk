# Plan 004: Bound query growth in Content & Calendar changelists

> **Executor instructions**: Execute every step and verification gate. Stop and report on a STOP condition; do not remove useful columns merely to satisfy a query budget. When done, mark plan 004 `Implemented` in `advisor-plans/README.md` and record the commit.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- hub/admin.py prayers/admin.py icons/admin.py learning_resources/admin.py tests/unit/test_admin_changelist_queries.py`
> Expected drift from plans 002 or 003 may be reconciled. Stop if unrelated changes altered the named ModelAdmin methods or relationship semantics.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: perf
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Several changelists fetch related objects or counts once per displayed row. That makes page latency grow linearly and turns otherwise useful image, relationship, and statistics columns into operational liabilities. Explicit queryset loading and database annotations can keep the same editor-facing information while making query counts essentially independent of row count.

## Current state

- `hub/admin.py:437-457`: Fast annotates participants but does not `select_related("church")` or prefetch ordered Days; both display methods dereference relations.
- `hub/admin.py:643-713`: Profile and Day render user/church/Fasts/Readings with no queryset optimization.
- `hub/models.py:515-528` and `hub/admin.py:311-313`: each Devotional Set list row runs a Devotional count query through the `number_of_days` property.
- `hub/admin.py:1512-1568`: Patristic Quote reads three many-to-many managers per row and calls `exists()` before reading Fasts.
- `hub/admin.py:1626-1629`: Passage Text runs a Reading count query for every row.
- `prayers/admin.py:85-86`: Prayer selects only Icon while the list also renders Church, Fast, Video, and Tags.
- `prayers/admin.py:166-167,209-213`: Prayer Set selects only Icon and counts prayers per row.
- `prayers/admin.py:484-494`: Prayer Request counts Acceptances and Prayer Logs per row with no optimized queryset.
- `icons/admin.py:39-41,77-78`: Icon reads Tags per row; Icon Feedback dereferences Icon per row.
- `learning_resources/admin.py:141-145`: Bookmark selects User and ContentType but dereferences a heterogeneous GenericForeignKey per row.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Query tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_changelist_queries --settings=tests.test_settings` | exit 0; constant-growth assertions pass |
| Existing affected tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.hub.test_admin prayers.tests.test_import_admin icons.tests.test_icon_caching tests.unit.learning_resources.test_bookmark_models --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Suggested executor toolkit

- Use the `django-patterns` skill for `select_related`, `Prefetch`, annotations, `OuterRef`, and `Subquery` choices.
- Inspect generated SQL only in tests or a local shell; never against production as part of this plan.

## Scope

**In scope**:

- `hub/admin.py`
- `prayers/admin.py`
- `icons/admin.py`
- `learning_resources/admin.py`
- `tests/unit/test_admin_changelist_queries.py` (create)

**Out of scope**:

- Model, index, constraint, or migration changes.
- Public API querysets.
- Cache behavior, thumbnail generation, background tasks, and admin actions.
- Removing columns solely to improve measured query counts.

## Git workflow

- Work on `codex/admin-improvement-audit`.
- Suggested commit: `perf: optimize content admin changelists`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Establish query-growth regression tests

Create `tests/unit/test_admin_changelist_queries.py`. Render each target changelist through the Django test client with a staff superuser and compare query counts for a small fixture and a fixture at least five times larger. Warm ContentType and permission caches consistently before capture. Assert query growth is bounded rather than asserting one fragile absolute count.

Cover at minimum Fast, Profile, Day, Devotional Set, Patristic Quote, Passage Text, Prayer, Prayer Set, Prayer Request, Icon, Icon Feedback, and Bookmark. Taggit and GenericForeignKey loading may require a small bounded number of queries per relation/content type; row-count growth must remain flat.

Because repository guidance warns that `@cache_page` contaminates parallel workers, decorate any cache-sensitive test class with the documented dummy-cache `override_settings` configuration.

**Verify**: run the new tests before implementation and confirm they fail for representative current N+1 cases. Do not commit a deliberately failing state.

### Step 2: Optimize Hub list querysets

Add or extend `get_queryset()` methods:

- Devotional: `select_related("video", "day", "day__fast", "day__church")`.
- Devotional Set: `select_related("fast")` and annotate the devotional count through `fast__days__devotionals`; use `distinct=True` if joins can multiply rows. The admin count method must prefer the annotation and only fall back to the model property outside annotated admin querysets.
- Fast: `select_related("church")`, prefetch Days in displayed order, and preserve the participant annotation with correct distinct semantics.
- Profile: `select_related("user", "church")` and prefetch Fasts.
- Day: `select_related("church", "fast", "fast__church")` as required by the final display implementation and prefetch Readings in their display order.
- Reading/Reading Context/Feast/Feast Context: add `select_related` for every FK dereferenced by list methods or `__str__` calls.
- Patristic Quote: prefetch Churches, Fasts, and Tags; ensure display methods consume the prefetched managers without issuing redundant queries.
- Fast Intention: select User and Fast.
- Passage Text: annotate `readings_served` using a correlated subquery grouped by `passage_key`, with `Coalesce(..., 0)` and an explicit integer output field. Do not add a fake model relation or migration.

Use unique annotation names such as `_admin_participant_count`, `_admin_devotional_count`, and `_admin_readings_served` to avoid colliding with model properties.

**Verify**: Hub query-growth tests pass and display values match direct ORM counts.

### Step 3: Optimize Prayer list querysets

- Prayer: select Church, Fast, Video, and Icon; prefetch Tags.
- Prayer Set: select Church and Icon; annotate count from `memberships` with distinct semantics.
- Prayer Request: select Requester and Icon; annotate Acceptance and Prayer Log totals with two separate `Count(..., distinct=True)` expressions so the joins do not multiply each other.
- Acceptance and Prayer Log: select Prayer Request and User because their list display and string representations dereference both.

Update display methods to use annotations when present. Preserve existing model methods for non-admin callers.

**Verify**: Prayer query-growth tests pass; annotated counts equal direct counts for fixtures where a request has multiple acceptances and multiple logs.

### Step 4: Optimize Icon and Bookmark list querysets

- Icon: select Church and prefetch Tags.
- Icon Feedback: select Icon.
- Bookmark: preserve User/ContentType `select_related` and prefetch `content_object`. Django 5.2 supports heterogeneous GenericForeignKey prefetching; expect one bounded query per represented content type rather than one per row. If the local Django version does not support this exact call, stop and report rather than adding hand-rolled cache mutation.

**Verify**: tests with multiple rows of the same content type show flat growth; a mixed Bookmark fixture has bounded growth by content-type count.

### Step 5: Validate values and full behavior

For every annotation, test zero, one, and multiple related rows. Confirm list ordering and filtering still return distinct parent rows when M2M filters are active.

**Verify**: run affected existing tests, then `scripts/crabbox-validate.sh ci`; both exit 0.

## Test plan

The new query test module must include:

- row-growth comparisons for every named admin;
- exact-value assertions for every annotated count;
- no duplicate parent rows under M2M filtering;
- zero-related-row behavior;
- Bookmark fixtures with one and multiple content types;
- dummy cache settings on any cache-sensitive class.

Prefer `CaptureQueriesContext` and rendered changelist responses. Do not assert SQL strings tied to a specific backend except to prove the PassageText subquery exists.

## Done criteria

- [ ] Every named changelist has bounded query growth with row count.
- [ ] Count annotations return correct values without join multiplication.
- [ ] No useful column is removed.
- [ ] Existing actions, filters, and ordering still work.
- [ ] Focused tests, affected tests, full validation, and diff checks pass.
- [ ] No migration is created.
- [ ] Plan 004 is marked Implemented.

## STOP conditions

- Optimization appears to require a schema/index migration.
- Heterogeneous GFK prefetch is unsupported by the installed Django 5.2 release.
- Query-count tests are unstable after cache warming and two reasonable fixture/test corrections.
- A count annotation changes list cardinality or produces incorrect multi-relation totals.
- Any action or public model method would need behavior changes.

## Maintenance notes

- Query-growth tests intentionally use relative budgets; update them only when a justified new fixed query is introduced.
- Reviewers should scrutinize `distinct=True`, M2M result duplication, and the PassageText correlated subquery.
- Plans 005 and 006 depend on the annotation names introduced here; keep those names documented in code.
