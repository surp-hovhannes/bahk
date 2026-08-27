# Plan 005: Turn Prayer Requests into a focused moderation queue

> **Executor instructions**: Complete this plan only after plans 002 and 004 are implemented. Run every verification gate. Stop and report when a STOP condition occurs; do not bypass Prayer Request business actions with direct field edits. When done, update plan 005 in `advisor-plans/README.md` to `Implemented` and record the commit.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- prayers/admin.py static/admin/css/fastandpray-admin.css prayers/tests/test_admin_moderation.py`
> Drift from plans 002 and 004 is required. Confirm that `PrayerRequestAdmin` now has the shared preview helper and annotated counts; otherwise stop because dependencies are incomplete.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: `advisor-plans/002-standardize-admin-media-previews.md`, `advisor-plans/004-optimize-admin-changelists.md`
- **Category**: direction
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Prayer Requests are a human-safety moderation workflow, but the current eleven-column table gives every attribute equal weight, hides submitted media, and makes urgent review states hard to scan. A compact queue with explicit attention filters, accessible state badges, thumbnails, and bounded counts will reduce moderation time without changing approval/rejection side effects.

## Current state

- `prayers/admin.py:387-399` lists title, requester, five status-related fields, duration, expiration, acceptance count, and created time in one wide row.
- `prayers/admin.py:400-408` exposes seven independent filters but no task-oriented “needs attention” filter.
- `prayers/admin.py:423` defines bulk actions. In particular, `approve_requests` performs event, milestone, and auto-acceptance behavior; inline editing of `status` would bypass these effects.
- `prayers/admin.py:425-473` already groups edit fields and has an image/Icon fallback preview, but the preview is absent from the changelist before plan 002.
- `prayers/admin.py:484-494` delegates counts to model methods; plan 004 replaces list-time calls with annotations.
- `PrayerRequest.Meta` orders newest first and indexes status/review/expiration fields (`prayers/models.py:403-411`). No migration is needed.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Moderation tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test prayers.tests.test_admin_moderation --settings=tests.test_settings` | exit 0 |
| Prayer Request regressions | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.test_prayer_requests --settings=tests.test_settings` | exit 0 |
| Dependency query tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_changelist_queries --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Suggested executor toolkit

- Use `django-patterns` for custom `SimpleListFilter`, annotations, and ModelAdmin tests.
- Use authenticated local browser QA to judge scanability at desktop and narrow widths after test success.

## Scope

**In scope**:

- `prayers/admin.py`
- `static/admin/css/fastandpray-admin.css`
- `prayers/tests/test_admin_moderation.py` (create)

**Out of scope**:

- Moderation prompts, LLM behavior, statuses, model fields, or migrations.
- Public Prayer Request APIs.
- Changing the side effects of approve, reject, or manually reviewed actions.
- `list_editable` for status/review fields.
- Making a hidden default filter that silently omits records.

## Git workflow

- Work on `codex/admin-improvement-audit` after plans 002 and 004.
- Suggested commit: `feat: streamline prayer request moderation`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add an explicit attention filter

Define `PrayerRequestAttentionFilter(admin.SimpleListFilter)` near `PrayerRequestAdmin` with clear choices:

- `needs_review`: `requires_human_review=True` or unreviewed pending moderation;
- `pending`: `status="pending_moderation"`;
- `expired_active`: approved requests whose `expiration_date` is in the past;
- `resolved`: completed, rejected, or deleted records.

The default/no-value state must show all records. Use `timezone.now()` and `Q` expressions; do not load records in Python. Place this filter first, followed by the existing field filters.

**Verify**: tests assert each choice's exact record membership and that no parameter shows every record.

### Step 2: Replace the wide row with a triage-oriented display

Use a compact `list_display` in this order:

1. media preview from plan 002;
2. title;
3. requester;
4. one combined, accessible moderation-state display;
5. expiration display;
6. annotated acceptances;
7. created time.

The combined moderation display must include visible text, not color alone, for status, severity, and human-review state. The expiration display should distinguish active, expired, and completed/rejected states without implying that rejected requests merely expired. Preserve access to all original values in filters and the change form.

Use `@admin.display` metadata for labels and ordering where backed by one field. Do not use inline style strings.

**Verify**: tests inspect list-display order and rendered labels for low/high severity, human review, approved active, expired approved, rejected, and completed records.

### Step 3: Add date navigation and result sizing

Set `date_hierarchy = "created_at"`, retain newest-first ordering, and set a deliberate `list_per_page` of 50 so media and moderation rows remain manageable. Preserve the existing search fields.

Use `list_select_related` or the plan 004 queryset rather than duplicating incompatible query-loading declarations.

**Verify**: changelist client tests assert hierarchy links, pagination, search, and combined attention/status filters.

### Step 4: Add shared state styling

Extend `static/admin/css/fastandpray-admin.css` with small admin-state badge classes using existing light/dark brand variables. Provide neutral, positive, warning, and critical variants with sufficient text contrast and a non-color cue where practical. Do not use the bright accent as normal-size text on white.

**Verify**: `rg -n 'style=' prayers/admin.py` → no newly introduced inline style for queue presentation.

### Step 5: Preserve action-only state transitions

Keep status and reviewed fields out of `list_editable`. Run existing action tests and add explicit regression coverage proving approval still creates its required events/acceptance behavior and rejection/manual-review actions still use their current code paths.

**Verify**: Prayer Request regression tests pass unchanged.

### Step 6: Perform moderator workflow QA

Seed local non-sensitive test requests covering each state. At desktop and narrow widths, verify that an editor can find needs-review records, identify media, open the record, act on it, and return to the filtered queue. Check keyboard navigation and dark mode.

**Verify**: full validation exits 0 after browser QA.

## Test plan

Create `prayers/tests/test_admin_moderation.py` covering:

- all attention-filter branches and the unfiltered default;
- combined state/expiration rendering and accessible text;
- list column order, thumbnail inclusion, date hierarchy, and pagination;
- annotated count values and dependency query budget;
- search combined with attention/status filters;
- explicit absence of `list_editable` state fields;
- approval/rejection/manual-review action regressions.

## Done criteria

- [ ] Prayer Requests have an explicit, non-default attention filter.
- [ ] The list is compact and exposes media, state, expiration, engagement, and creation date.
- [ ] Status meaning is conveyed with text and not color alone.
- [ ] Counts remain annotated and query growth stays bounded.
- [ ] State transitions still run through existing actions.
- [ ] Focused, regression, query, full validation, and diff checks pass.
- [ ] No migration is created.
- [ ] Plan 005 is marked Implemented.

## STOP conditions

- Plans 002 or 004 are not implemented or use incompatible preview/count contracts.
- A UI shortcut would bypass existing action side effects.
- Product policy is required to redefine what “needs review,” “resolved,” or “expired active” means.
- Tests reveal existing action behavior is already inconsistent; report it separately instead of folding a behavioral repair into this plan.
- A migration is generated.

## Maintenance notes

- Keep the no-filter state complete; moderators must be able to audit all records.
- Any future status must be added to the combined display and filter tests.
- Reviewers should focus on safety semantics, action preservation, and accessible non-color state communication.
