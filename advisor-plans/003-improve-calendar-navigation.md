# Plan 003: Make calendar records easy to navigate by date, church, fast, and language

> **Executor instructions**: Follow this plan step by step and run every verification gate. Stop and report instead of improvising when a STOP condition occurs. When done, update plan 003 in `advisor-plans/README.md` to `Implemented` and record the implementation commit.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- hub/admin.py tests/unit/hub/test_admin_calendar.py`
> Changes made by declared dependencies are expected. For any other drift, compare the symbols below against live code and stop if their contracts changed.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Day, Reading, and Devotional are the core calendar-editing workflow, but their changelists expose different date controls and omit important church, fast, or language context. Reading's custom year filter computes choices by iterating every Reading and dereferencing its Day, so opening the filter can become progressively slower as the calendar grows. Consistent database-backed navigation will make date-oriented editing predictable and scalable.

## Current state

- `hub/admin.py:100-149`: Devotional orders by ascending day date and filters by fast and date, but does not display or filter `language_code` and has no date hierarchy.
- `hub/admin.py:687-715`: Day displays date plus related links and filters only by church and fast; there is no date hierarchy or search.
- `hub/admin.py:696-700`: Day's church link reads `day.fast.church` even though `Day` has its own authoritative `church` field (`hub/models.py:380-395`).
- `hub/admin.py:718-724`: `ReadingYearFilter.lookups()` loops over every Reading and evaluates `r.day.date.year` in Python.
- `hub/admin.py:735-764`: Reading filters by year, book, chapter, and verse but not by church or fast; it has no date hierarchy.
- `hub/models.py:541-558`: Devotional has `day`, `video`, `order`, and `language_code`; language is row-level operational context, not merely a translated virtual field.
- Avoid migrations. Every required field and index already exists.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.hub.test_admin_calendar --settings=tests.test_settings` | exit 0 |
| Existing hub admin tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.hub.test_admin hub.tests.test_admin_compare_reading_prompts hub.tests.test_feast_admin_icon_actions --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Suggested executor toolkit

- Use the `django-patterns` skill for queryset and ModelAdmin conventions.
- Use browser QA after tests to verify date hierarchy behavior rather than relying only on class attributes.

## Scope

**In scope**:

- `hub/admin.py`
- `tests/unit/hub/test_admin_calendar.py` (create)

**Out of scope**:

- Calendar model fields, constraints, or migrations.
- Devotional creation business logic and existing custom endpoints/actions.
- Reading text retrieval, budgets, and PassageText behavior.
- Global navigation and non-calendar apps.

## Git workflow

- Work on `codex/admin-improvement-audit`.
- Suggested commit: `feat: improve admin calendar navigation`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Replace the Python year scan

Keep `ReadingYearFilter` so editors retain explicit historical-year choices, but implement `lookups()` as one database query using `values_list("day__date__year", flat=True)`, `order_by`, and `distinct`. Exclude nulls defensively even though Day.date is required. Return stable ascending `(year, year)` choices.

Keep the selected-year queryset database-backed. Prefer `day__date__year=year` unless generated SQL or index use in the supported database makes the current bounded date range preferable. Do not evaluate rows in Python.

**Verify**: focused tests assert the choices are unique, ascending, and produced without loading Day model instances. Expected: exit 0.

### Step 2: Make Day navigation date-first

In `DayAdmin`:

- set `date_hierarchy = "date"`;
- retain church and fast filters;
- add `search_fields` for church and fast names (date lookup remains through hierarchy/filter controls);
- have `church_link` use `day.church`, not `day.fast.church`;
- decorate church and fast link methods with database ordering fields;
- preserve the existing reading links and default church/date ordering.

If plan 004 has already landed, preserve its `select_related`/`prefetch_related` queryset optimizations.

**Verify**: tests create a Day whose `church` differs from its Fast's church and assert the displayed link uses `Day.church`. This is an admin-display correction only; do not change the data.

### Step 3: Align Reading filters and hierarchy

In `ReadingAdmin`:

- set `date_hierarchy = "day__date"`;
- add `day__church` and `day__fast` to the beginning of `list_filter`, followed by `ReadingYearFilter` and the existing passage filters;
- retain `passage_key` search and add translated book-name search only if the live model fields support it without invalid lookup errors;
- preserve existing text-fetch and context actions.

**Verify**: use the Django test client to load the Reading changelist and assert church, fast, and year filter parameters produce the expected subsets.

### Step 4: Expose Devotional language and date navigation

In `DevotionalAdmin`:

- add `language_code` immediately before `order` in `list_display`;
- add `language_code` to `list_filter`;
- set `date_hierarchy = "day__date"`;
- order newest dates first unless an existing test or documented workflow requires chronological ascending order; preserve `order` and language as deterministic tie-breakers;
- keep title, fast, and date sortable.

Do not remove the combined devotional creation UI or change uniqueness behavior.

**Verify**: focused tests assert English/Armenian filtering, newest-date-first ordering, and date hierarchy links.

### Step 5: Perform focused browser QA

Using a migrated local test database, check Day, Reading, and Devotional at desktop and narrow widths. Confirm that hierarchy navigation, filters, search, and ordering can be combined; selected filters remain visible; and browser back/forward navigation works.

**Verify**: `scripts/crabbox-validate.sh ci` → exit 0 after browser QA.

## Test plan

Create `tests/unit/hub/test_admin_calendar.py` with:

- `ReadingYearFilter` distinct/sorted lookup coverage and a query-shape assertion that it does not instantiate Days;
- Day authoritative-church display and filters;
- Day, Reading, and Devotional date hierarchy declarations;
- Reading church/fast/year filter integration tests;
- Devotional language list/filter and deterministic ordering tests;
- admin changelist GET smoke tests for all three models.

Use Django's test client and `RequestFactory`; use the existing admin superuser pattern in `tests/unit/test_admin_branding.py`.

## Done criteria

- [ ] Reading year choices come from one set-based query and are stable.
- [ ] Day, Reading, and Devotional provide coherent date navigation.
- [ ] Reading exposes church and fast filters.
- [ ] Devotional exposes language and deterministic newest-first ordering.
- [ ] Day displays its own church.
- [ ] Focused, existing hub, full validation, and diff checks pass.
- [ ] No migrations are created.
- [ ] Plan 003 is marked Implemented.

## STOP conditions

- Django 5.2 does not support the proposed related-field date hierarchy in this project configuration; report the exact failure instead of adding a custom template without review.
- A change would alter Day uniqueness or devotional creation behavior.
- A new migration appears.
- Existing tests document chronological ascending Devotional ordering as a required editor contract.
- Validation fails twice for an introduced regression.

## Maintenance notes

- Keep calendar filtering database-backed; never rebuild year choices by iterating model instances.
- Reviewers should verify Day's authoritative church behavior against production data expectations.
- Plan 007 adds broader search/navigation polish and must preserve these calendar controls.
