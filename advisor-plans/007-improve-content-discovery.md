# Plan 007: Improve Content & Calendar search, filtering, and persistent navigation

> **Executor instructions**: Complete this plan after plan 003. Run every verification gate. Keep Django's permission filtering authoritative and stop if the grouped-sidebar approach would expose a model the current user cannot access. Mark plan 007 `Implemented` in `advisor-plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- bahk/admin_site.py hub/admin.py learning_resources/admin.py templates/admin/nav_sidebar.html static/admin/css/fastandpray-admin.css tests/unit/test_admin_discovery.py tests/unit/test_admin_branding.py`
> Drift from selected predecessor plans is expected. Stop on unrelated changes to admin permission/grouping behavior.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: `advisor-plans/003-improve-calendar-navigation.md`
- **Category**: direction
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Several high-use models cannot be searched by the identifiers editors actually know, Bookmark's standard ContentType filter lists every installed Django model, and the Content & Calendar grouping exists only on the home dashboard. Search and navigation should preserve Django permissions while reflecting the editor's domain instead of the underlying package layout.

## Current state

- `hub/admin.py:81-96`: Church has no `search_fields`.
- `hub/admin.py:420-479`: Fast has no `search_fields` despite names, descriptions, years, and Church relationships.
- `hub/admin.py:643-684`: Profile has no search despite user email/name, profile name, and location.
- Plan 003 adds calendar-specific Day search. This plan must preserve it.
- `learning_resources/admin.py:83-90`: Bookmark uses the stock `content_type` filter. In the authenticated local UI this showed every installed model, including auth, sessions, Celery, analytics, and unrelated operational tables.
- `learning_resources/serializers.py:284-315` documents nine bookmarkable model names, but an admin list filter only needs to show ContentTypes actually present in the current Bookmark queryset; it need not duplicate application validation.
- `bahk/admin_site.py:18-38` defines Content & Calendar and other task-oriented sections.
- `bahk/admin_site.py:139-150` supplies grouped sections only to the index template. Django's normal sidebar continues to show separate raw app groups on model pages.
- All app/model visibility must continue to come from Django's permission-filtered `get_app_list()` output.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Discovery tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_discovery --settings=tests.test_settings` | exit 0 |
| Branding/navigation tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_branding --settings=tests.test_settings` | exit 0 |
| Calendar tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.hub.test_admin_calendar --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Suggested executor toolkit

- Use `django-patterns` for admin search and `SimpleListFilter` behavior.
- Use authenticated browser QA for sidebar focus, keyboard navigation, and responsive behavior.

## Scope

**In scope**:

- `bahk/admin_site.py`
- `hub/admin.py`
- `learning_resources/admin.py`
- `templates/admin/nav_sidebar.html` (create)
- `static/admin/css/fastandpray-admin.css`
- `tests/unit/test_admin_discovery.py` (create)
- `tests/unit/test_admin_branding.py`

**Out of scope**:

- Model metadata/verbose-name migrations.
- Public Bookmark validation or API behavior.
- Hiding apps/models that Django says the user may access.
- Adding fuzzy search or an external search service.
- Changing global permission logic or model registration.

## Git workflow

- Work on `codex/admin-improvement-audit` after plan 003.
- Suggested commit: `feat: improve content admin discovery`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add editor-oriented search fields

Configure:

- Church: name and translated name fields that exist in the live model.
- Fast: name/translated names, description, Church name, and exact year search using Django's `=` prefix where supported.
- Profile: User email, username, first/last names, profile name, and location.
- Day: preserve plan 003's Church/Fast search; add exact ISO date search only if Django's generated lookup is valid on every supported test database.

Retain existing search fields on all other scoped models. Add `search_help_text` to these admins when it clarifies accepted identifiers, especially exact year/date syntax.

**Verify**: test-client queries return the expected records for email, profile name, location, Fast name, Church name, and year. Invalid input must return a normal empty/result page, not a 500.

### Step 2: Replace Bookmark's global ContentType filter

Create `BookmarkContentTypeFilter(admin.SimpleListFilter)` in `learning_resources/admin.py`. Its lookups must be built from distinct ContentTypes present in the current permitted Bookmark queryset, ordered by human-readable app/model labels. The queryset method filters by validated numeric ContentType ID.

The filter must not enumerate every row, expose unrelated ContentTypes with no Bookmark rows, or change what values the Bookmark form/API accepts. Preserve the created-date filter.

If filtering the lookups according to the requesting admin user's object visibility is not possible with current model permissions, use the already permission-protected Bookmark changelist queryset and document that choice in a comment; do not inspect private registry internals.

**Verify**: fixtures with Bookmarks for Video and Prayer show only those two choices; unrelated ContentTypes are absent; each choice filters correctly.

### Step 3: Supply grouped sections on every admin page

Override `FastAndPrayAdminSite.each_context()` and call `super().each_context(request)` first. Derive `admin_sections` from the returned `available_apps` using the existing `group_app_list()` method. Never build navigation from `_registry` or a global unfiltered app list.

Avoid duplicate work on the index: update `index()` to reuse the context value where possible while preserving quick-action permission behavior.

**Verify**: extend branding tests for superuser, limited staff user, and user with no model permissions. Every grouped model URL must be a subset of `available_apps`; empty sections must be omitted.

### Step 4: Add a section-aware sidebar override

Create `templates/admin/nav_sidebar.html` based on the installed Django 5.2 template's semantic structure, but render `admin_sections`, their apps, and their models. Preserve:

- the “Filter navigation items” control and the element IDs/classes used by Django's `nav_sidebar.js`;
- app and model links from permission-filtered context;
- current app/model highlighting and `aria-current` behavior;
- add links only where Django supplied `add_url`;
- all unmatched apps under the existing Other section from `group_app_list()`.

Do not hard-code model URLs. If retaining Django's filter behavior would require copying large or unstable private markup, stop and report rather than ship a non-filterable sidebar.

Add only minimal shared CSS for section labels and indentation.

**Verify**: template tests assert model permissions, current-item state, filter input, and all section headings. Existing branding tests remain green.

### Step 5: Browser-check discovery workflows

At desktop and narrow widths, verify:

- the sidebar retains Content & Calendar grouping on a model list and edit page;
- filtering the sidebar still works;
- current model highlighting is visible and screen-reader state is present;
- keyboard traversal follows visible order;
- Bookmark filters list only represented content types;
- Church/Fast/Profile/Day searches work and combine with filters.

Check light and dark themes.

**Verify**: full validation exits 0 after browser QA.

## Test plan

Create `tests/unit/test_admin_discovery.py` for:

- search results and invalid/no-result cases on Church, Fast, Profile, and Day;
- Bookmark content-type lookups, ordering, filtering, and absence of unrelated models;
- query count for filter lookups remains fixed rather than row-dependent;
- grouped navigation context on non-index admin pages.

Extend `tests/unit/test_admin_branding.py` for:

- permission-safe sectioned sidebar rendering;
- unmatched-app fallback;
- empty-section omission;
- current link and nav-filter DOM contracts.

## Done criteria

- [ ] Church, Fast, Profile, and Day support editor-oriented search.
- [ ] Bookmark filter choices are limited to represented content types.
- [ ] Content & Calendar grouping persists through list and edit navigation.
- [ ] Sidebar remains filterable, responsive, keyboard accessible, and permission safe.
- [ ] Discovery, branding, calendar, full validation, and diff checks pass.
- [ ] No migrations are created.
- [ ] Plan 007 is marked Implemented.

## STOP conditions

- Grouped navigation would require bypassing `available_apps` or reconstructing permissions.
- Django 5.2's sidebar JavaScript cannot operate with the proposed semantic override without copying unstable private code.
- A useful search requires a schema/index migration.
- Bookmark lookup filtering changes API/form validation rather than presentation only.
- A new migration appears.

## Maintenance notes

- New apps automatically fall into Other; add them to `section_definitions` only after an explicit IA decision.
- Keep sidebar tests tied to accessibility/permission contracts, not whitespace.
- Plan 008 relies on the search fields established here for autocomplete endpoints.
