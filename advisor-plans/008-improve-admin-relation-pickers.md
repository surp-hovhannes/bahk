# Plan 008: Replace opaque raw-ID relationship controls with searchable pickers

> **Executor instructions**: Complete this plan last, after plans 002, 003, and 007. Run every verification gate. Stop if an autocomplete would weaken object permissions or require a public endpoint. Mark plan 008 `Implemented` in `advisor-plans/README.md` when complete.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- hub/admin.py prayers/admin.py learning_resources/admin.py tests/unit/test_admin_autocomplete.py`
> Drift from the declared dependencies is required. Confirm their preview and search-field contracts exist before continuing.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: `advisor-plans/002-standardize-admin-media-previews.md`, `advisor-plans/003-improve-calendar-navigation.md`, `advisor-plans/007-improve-content-discovery.md`
- **Category**: dx
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Content editors currently select many important relationships by numeric raw ID. That is efficient for large tables but forces editors to know IDs and makes it easy to choose the wrong Day, Video, Icon, Fast, Feast, Prompt, Prayer, or User. Django's permission-aware autocomplete widgets provide scalable search while persisted media previews let editors visually verify image-bearing selections.

## Current state

- `hub/admin.py:105`: Devotional uses raw IDs for Video and Day.
- `hub/admin.py:271`: Devotional Set uses raw ID for Fast.
- `hub/admin.py:1176,1212,1483,1526,1577`: Reading Context, Feast, Feast Context, Patristic Quote, and Fast Intention use raw-ID relationships; Patristic Quote also declares `filter_horizontal` for the same M2M fields.
- `prayers/admin.py:65-72`: Prayer Set Membership inline selects Prayer by raw ID.
- `prayers/admin.py:82,155,410,596,609`: Prayer, Prayer Set, Prayer Request, Acceptance, and Prayer Log use raw IDs.
- `learning_resources/admin.py:92`: Bookmark uses raw ID for User while ContentType and object ID remain a generic pair.
- Django admin autocomplete endpoints inherit model permissions and depend on `search_fields` on the related model's registered ModelAdmin. Plans 003 and 007 establish missing search contracts.
- Plan 002 adds persisted image/Icon/Video previews. This plan must reuse them and must not create a second renderer.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Autocomplete tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_autocomplete --settings=tests.test_settings` | exit 0 |
| Discovery/calendar tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_discovery tests.unit.hub.test_admin_calendar --settings=tests.test_settings` | exit 0 |
| Prayer import tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test prayers.tests.test_import_admin --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Suggested executor toolkit

- Use `django-patterns` for admin autocomplete permissions and related-admin configuration.
- Use authenticated browser QA for Select2 keyboard/search behavior and persisted previews.

## Scope

**In scope**:

- `hub/admin.py`
- `prayers/admin.py`
- `learning_resources/admin.py`
- `tests/unit/test_admin_autocomplete.py` (create)

**Out of scope**:

- Model `__str__` changes that affect public/API representations.
- Custom public endpoints or unauthenticated lookup APIs.
- GenericForeignKey autocomplete for Bookmark's `object_id`.
- JavaScript thumbnail results inside the autocomplete dropdown.
- Schema, index, or migration changes.

## Git workflow

- Work on `codex/admin-improvement-audit` after plans 002, 003, and 007.
- Suggested commit: `feat: add searchable admin relation pickers`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Verify related-admin search and permission contracts

Create a test matrix mapping each intended autocomplete field to its related registered ModelAdmin and required search identifiers. Cover:

- Church: name;
- Fast: name, Church, year;
- Day: Church, Fast, date;
- Video: title/description;
- Icon: title/tags/hash;
- Reading: passage/book/date context;
- LLMPrompt: role/model/prompt;
- Feast: name/designation/Church;
- Prayer: title/text;
- User: existing Django UserAdmin identity fields;
- Prayer Request: title/requester identity.

Add only missing `search_fields` required for these widgets. Keep exact/numeric/date lookup prefixes where they avoid expensive broad matching. Do not broaden anonymous access; admin autocomplete remains staff-only and permission checked.

**Verify**: tests issue autocomplete requests as superuser, limited staff with view permission, and staff without related-model permission. Results must be searchable and permission-safe.

### Step 2: Convert core authoring relationships

Replace `raw_id_fields` with `autocomplete_fields` for:

- Devotional: Video and Day;
- Devotional Set: Fast;
- Feast: Icon;
- Reading Context: Reading and Prompt;
- Feast Context: Feast and Prompt;
- Fast Intention: User and Fast;
- Prayer: Church, Fast, Video, and Icon;
- Prayer Set: Church and Icon;
- Prayer Set Membership inline: Prayer;
- Prayer Request: Requester and Icon;
- Prayer Request Acceptance/Prayer Log: Prayer Request and User;
- Bookmark: User only.

Leave Bookmark ContentType/object ID as-is because a GenericForeignKey needs a separate specialized design. Preserve nullable/required behavior.

For Patristic Quote, use `autocomplete_fields` for Churches and Fasts and remove those fields from both `raw_id_fields` and `filter_horizontal`; one field must never be configured by competing widgets.

**Verify**: ModelAdmin configuration tests assert no field is present in more than one relation-widget declaration.

### Step 3: Make autocomplete result labels informative without changing model strings globally

Where an existing `__str__` already contains enough context, keep it. Where it does not, override `get_search_results()` or use a narrowly scoped admin/widget label mechanism only if supported cleanly by Django 5.2. Do not change model `__str__` merely for admin unless every non-admin caller is audited.

Minimum distinguishability:

- Day results show date, Fast when present, and Church;
- Fast results show name, year, and Church when needed;
- Icon and Video results show title, with Church/category context where duplicates are plausible;
- Prompt results show model, applies-to, role, and active state;
- User results show email plus name where available.

If Django's built-in autocomplete JSON labels cannot be customized cleanly without overriding a private view, retain safe model labels and document the limitation; do not create a public endpoint.

**Verify**: autocomplete response tests assert distinguishing text for duplicate-name fixtures.

### Step 4: Reuse persisted relationship previews

On edit forms for saved objects, keep the plan 002 readonly previews immediately adjacent to Icon/Video/image relationship controls. The preview may update after save/reload; dynamic unsaved selection preview is explicitly out of scope. Add concise help text explaining that the preview reflects the saved relationship if this distinction could confuse editors.

**Verify**: form HTML contains both the autocomplete widget contract and the persisted preview for saved Feast, Devotional, Prayer, Prayer Set, and Prayer Request fixtures.

### Step 5: Browser-check picker workflows

Using only non-sensitive local data, test keyboard and mouse selection for each relationship category. Search duplicate names, select a result, save a test object, and verify the persisted preview/relationship. Do not perform destructive edits to user-owned records; create disposable local fixtures.

Check narrow and desktop widths, dark mode, no-result behavior, permission denial, and inline Prayer selection.

**Verify**: full validation exits 0 after browser QA.

## Test plan

Create `tests/unit/test_admin_autocomplete.py` covering:

- the field-to-related-admin search matrix;
- superuser, limited-permission, and denied autocomplete requests;
- query filtering and duplicate-name distinguishability;
- widget configuration for every converted field;
- no raw/autocomplete/filter-horizontal conflicts;
- nullable and required form behavior;
- saved relationship previews from plan 002;
- Prayer Set import/edit and inline ordering regressions.

## Done criteria

- [ ] Every listed raw-ID relationship is converted to permission-aware autocomplete.
- [ ] Patristic Quote has exactly one widget configuration per M2M field.
- [ ] Duplicate-name search results provide enough context where Django's supported hooks allow it.
- [ ] Saved image-bearing relations retain visual previews.
- [ ] Autocomplete, dependency, import, full validation, and diff checks pass.
- [ ] No public endpoint or migration is added.
- [ ] Plan 008 is marked Implemented.

## STOP conditions

- Any declared dependency is incomplete.
- A related model lacks a safe, indexed-enough search strategy without schema work.
- Autocomplete exposes results to a user lacking related-model permission.
- Informative labels require overriding private Django internals or changing broadly used model `__str__` methods.
- A field's current custom widget/business behavior cannot be preserved.
- A migration is generated.

## Maintenance notes

- New autocomplete fields require related-admin search tests and permission coverage.
- Reviewers should test limited staff roles, not only superusers.
- Generic Bookmark target selection and thumbnail-rich dropdown results remain deliberate follow-ups, not omissions in this plan.
