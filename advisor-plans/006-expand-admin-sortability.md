# Plan 006: Make useful Content & Calendar columns sortable

> **Executor instructions**: Complete this plan after plan 004. Run every verification command. Stop on a STOP condition instead of implementing client-side sorting or changing model defaults. Mark plan 006 `Implemented` in `advisor-plans/README.md` when complete.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- hub/admin.py prayers/admin.py icons/admin.py learning_resources/admin.py tests/unit/test_admin_sorting.py`
> Drift from plan 004 and earlier selected plans is expected. Confirm the count annotations documented below exist before continuing.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: `advisor-plans/004-optimize-admin-changelists.md`
- **Category**: tech-debt
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Many lists display database-backed values that Django could sort, but wrapper methods lack ordering metadata or an explicit `sortable_by` allowlist blocks them. Editors therefore cannot answer routine questions such as which Fast culminates next, which Prayer Set has the most prayers, or which Church a Day belongs to without exporting data.

## Current state

- `hub/admin.py:422-434`: Fast shows Church and culmination date but `sortable_by` allows only name and participant count.
- `hub/admin.py:645-657`: Profile shows Church, name, and location but restricts sorting to user and joined date.
- `hub/admin.py:689-715`: Day link methods have no `admin_order_field` metadata.
- `hub/admin.py:267-313`: Devotional Set count is computed and unsortable before plan 004.
- `prayers/admin.py:152-213`: Prayer Set count is computed and unsortable before plan 004.
- `prayers/admin.py:484-494`: Prayer Request acceptance count is unsortable before plan 004.
- Multi-valued columns such as Tags, Fasts, Readings, and Churches do not have one honest ordering and must remain unsortable.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Sorting tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_sorting --settings=tests.test_settings` | exit 0 |
| Query tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_changelist_queries --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Scope

**In scope**:

- `hub/admin.py`
- `prayers/admin.py`
- `icons/admin.py`
- `learning_resources/admin.py`
- `tests/unit/test_admin_sorting.py` (create)

**Out of scope**:

- Model `Meta.ordering`, indexes, fields, or migrations.
- Sorting inherently multi-valued columns by an arbitrary first value.
- JavaScript/client-side sorting.
- Changing default list ordering except where a prior selected plan explicitly does so.

## Git workflow

- Work on `codex/admin-improvement-audit` after plan 004.
- Suggested commit: `feat: expand useful admin sorting`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define an explicit honest sorting matrix

Before editing, encode expected sortable columns in tests:

- Fast: name, church, culmination date, participant count.
- Profile: user, church, name, location, joined date.
- Day: date, church, fast.
- Devotional: title, fast, date, language code, order.
- Devotional Set: title, fast, annotated devotional count, created time.
- Feast: church and name.
- Prayer: title, category, church, fast, video, created time.
- Prayer Set: title, category, church, annotated prayer count, created time.
- Prayer Request: title, requester, status/severity fields represented by the combined display when unambiguous, expiration, annotated acceptance count, created time.
- Icon: title, church, hashes, created time; Tags stays unsortable.
- Icon Feedback: Icon title, type, email, created time, resolved.
- Video/Article/Recipe/Bookmark: preserve all directly sortable fields already supplied by Django; content title remains unsortable.

**Verify**: initial tests fail only for the currently missing ordering metadata.

### Step 2: Add Django ordering metadata

Prefer `@admin.display(ordering="field_or_annotation", description="...")` over mutating function attributes after definition. Remove or expand restrictive `sortable_by` tuples so they match the tested matrix. Use annotation names from plan 004 for count columns.

Do not assign ordering to M2M summaries, preview images, truncated free text, or GenericForeignKey titles.

**Verify**: ModelAdmin metadata tests pass.

### Step 3: Verify actual result order

Use Django's ChangeList/test client with `?o=` parameters derived from rendered header links. Create fixtures whose alphabetical, date, and count orders differ. Assert ascending and descending results for Fast culmination date, Profile location, Day church, Devotional Set count, Prayer Set count, and Prayer Request acceptances.

Keep null ordering database-portable: tests should assert relative order of non-null values and only assert null placement if explicitly configured with a Django `OrderBy` expression.

**Verify**: sorting tests and plan 004 query tests both pass.

### Step 4: Browser-check sort affordances

Inspect representative lists and confirm sortable headers have Django's standard affordance, current sort direction remains visible, and horizontal layout has not regressed. Check desktop and narrow widths.

**Verify**: `scripts/crabbox-validate.sh ci` exits 0.

## Test plan

Create `tests/unit/test_admin_sorting.py` with:

- a declared expected sorting matrix for every scoped ModelAdmin;
- positive assertions for honest fields/annotations;
- negative assertions for multi-valued and media/preview fields;
- end-to-end ascending/descending results for representative direct, related, and annotated fields;
- duplicate-row regression checks under annotated sorting.

## Done criteria

- [ ] Every matrix-approved column is sortable in both directions.
- [ ] Multi-valued, preview, and GenericForeignKey title columns remain unsortable.
- [ ] Annotated sorting does not duplicate rows or regress query budgets.
- [ ] Sorting, query, full validation, and diff checks pass.
- [ ] No model or migration changes exist.
- [ ] Plan 006 is marked Implemented.

## STOP conditions

- Plan 004 annotations are absent or named differently and cannot be reconciled without changing their tested contract.
- A requested sort requires arbitrary ordering of multiple related values.
- A sort would require a new database index or schema migration to be usable.
- Backend differences make a test depend on undocumented null ordering.

## Maintenance notes

- Keep the expected sorting matrix in tests; it documents deliberate unsortable columns as well as sortable ones.
- Reviewers should verify `sortable_by` cannot silently suppress newly decorated fields.
