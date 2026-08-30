# Plan 002: Standardize media previews across Content & Calendar admin

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If a STOP condition occurs, stop and report; do not improvise. When done, update plan 002 in `advisor-plans/README.md` to `Implemented` and record the implementation commit.
>
> **Drift check (run first)**: `git diff --stat 2f3b2e7..HEAD -- bahk/admin_media.py hub/admin.py prayers/admin.py icons/admin.py learning_resources/admin.py static/admin/css/fastandpray-admin.css tests/unit/test_admin_media.py`
> Changes from already-completed plans in this directory are expected. For any other drift, compare the current symbols with the excerpts below and stop if their contracts changed.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `2f3b2e7`, 2026-08-26

## Why this matters

Editors cannot visually verify the primary media on Icons, Fasts, Profiles, Feasts, or Devotionals. Existing previews in the other models duplicate fragile inline HTML, omit alternative text, and use inconsistent sizes. A shared renderer will make the list and edit workflows faster to scan, accessible, storage-safe, and visually consistent with the branded admin.

## Current state

- `icons/admin.py:10-27` lists hashes and the raw image field but no image preview.
- `hub/admin.py:422-473` renders a Fast's image as the text `Image Link`; `hub/admin.py:645-684` does the same for Profile images.
- `hub/admin.py:1197-1231` exposes a Feast's Icon as a raw ID without showing it.
- `hub/admin.py:100-149` shows Devotional title, fast, date, and order but not its Video thumbnail.
- `icons/admin.py:46-80` shows feedback about an Icon without showing the Icon itself.
- `prayers/admin.py:120-143`, `prayers/admin.py:169-205`, and `learning_resources/admin.py:30-75` each build `<img>` markup independently with inline dimensions and no `alt` text.
- `static/admin/css/fastandpray-admin.css` is the project-owned location for admin presentation. Keep layout rules there rather than adding new inline styles.
- Models already cache or generate thumbnail URLs. Do not alter model fields, storage behavior, image processors, or migrations.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused tests | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_media --settings=tests.test_settings` | exit 0; all new media tests pass |
| Branding regression | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_branding --settings=tests.test_settings` | exit 0 |
| Full validation | `scripts/crabbox-validate.sh ci` | exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Suggested executor toolkit

- Use the `django-patterns` skill for safe Django admin helpers and ORM-aware access.
- Use the in-app browser or Playwright only after the automated tests pass; verify light and dark themes at desktop and narrow widths.

## Scope

**In scope**:

- `bahk/admin_media.py` (create)
- `hub/admin.py`
- `prayers/admin.py`
- `icons/admin.py`
- `learning_resources/admin.py`
- `static/admin/css/fastandpray-admin.css`
- `tests/unit/test_admin_media.py` (create)

**Out of scope**:

- Model or migration changes.
- Thumbnail generation, S3 caching, upload paths, and image-processing settings.
- Public API serializers or response fields.
- JavaScript that previews an unsaved autocomplete selection; plan 008 handles relation-picker behavior.

## Git workflow

- Work on `codex/admin-improvement-audit`.
- Use a focused commit such as `feat: standardize admin media previews`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add a storage-safe shared renderer

Create `bahk/admin_media.py` with a single public helper, `admin_thumbnail`. It must:

- accept an object, an ordered sequence of URL-bearing attributes (for example `cached_thumbnail_url`, `thumbnail`, `image`), an accessible alt string, a size class, and fallback text;
- accept either a URL string or a Django file/image-like object whose `.url` is read lazily;
- use the first usable URL and catch only expected missing-file/storage access failures (`AttributeError`, `ValueError`, `OSError`);
- return `format_html` output with a project CSS class, `alt`, `loading="lazy"`, explicit width/height attributes, and an optional link to the full asset using `target="_blank" rel="noopener noreferrer"`;
- escape all object-derived content through `format_html`; never use `mark_safe`;
- return plain fallback text when no source is usable.

Do not perform a database query or write/cache a thumbnail inside this helper.

**Verify**: run the focused test command after creating helper tests for URL precedence, file-object fallback, escaping, missing media, expected exceptions, link security attributes, and alt text. Expected: exit 0.

### Step 2: Replace existing duplicate preview methods

Update the existing preview methods in Prayer, Prayer Set, Video, Article, Recipe, and Devotional Set admins to call `admin_thumbnail`. Preserve their public method names because templates and tests may reference them. Use the shared CSS sizes instead of inline style attributes.

Use meaningful alt strings such as `Icon for <title>`, `Thumbnail for <title>`, and `Profile photo for <email>`. Empty/fallback states must remain human-readable.

**Verify**: `rg -n '<img[^>]+style=' hub/admin.py prayers/admin.py icons/admin.py learning_resources/admin.py` → no media-preview inline styles remain in these four files.

### Step 3: Add missing first-class and related previews

Add preview methods and fields as follows:

- Icon: thumbnail in `list_display` immediately after title and a readonly preview next to `image` in the Image fieldset.
- Icon Feedback: Icon thumbnail in the changelist and readonly edit view, alongside the immutable submission snapshot.
- Fast: replace `image_link` with an image preview in the changelist and add a readonly preview adjacent to the image field in the change form.
- Profile: replace `profile_image_link` with a square preview in the changelist and add it beside the profile image field.
- Feast: show its related Icon thumbnail in the changelist and Icon fieldset.
- Devotional: show the related Video thumbnail in the changelist and Media/edit fields without making the Video itself editable inline.
- Prayer Request: add the already-defined `image_preview` to the changelist; retain its Icon fallback.

Use cached URLs before generated thumbnails and originals. Do not request a generated thumbnail when the object has no source image.

**Verify**: focused tests render each ModelAdmin method with media present and absent; expected: all pass and rendered HTML contains an accessible `<img>` only when media exists.

### Step 4: Add shared thumbnail styling

Add `.fp-admin-thumbnail` size variants to `static/admin/css/fastandpray-admin.css`. Use `object-fit: cover` for square/profile and 4:3 content variants, `object-fit: contain` for icons, the established admin border/radius tokens, and a visible keyboard focus state on linked images. Keep changelist rows compact; a small list preview must not exceed roughly 56 px in either dimension.

**Verify**: `git diff --check` → no output.

### Step 5: Perform visual regression checks

With a migrated local test database containing at least one record with and without media for each affected class, inspect:

- Icon list and edit;
- Fast and Profile lists;
- Feast and Devotional edit forms;
- Prayer Request list;
- existing Video, Article, Recipe, Devotional Set, Prayer, and Prayer Set previews.

Check desktop and narrow widths, light and dark themes, broken/missing media fallbacks, keyboard focus, and that list rows remain legible. Do not use production credentials or data.

**Verify**: `scripts/crabbox-validate.sh ci` → exit 0 after visual QA.

## Test plan

Create `tests/unit/test_admin_media.py` and cover:

- helper URL precedence and fallback behavior;
- HTML escaping and secure link attributes;
- alt text and lazy loading;
- each newly added ModelAdmin preview method with media and without media;
- existing preview admins now delegate to the helper;
- changelist/edit field declarations contain the new previews.

Use `SimpleUploadedFile` plus temporary local storage only where a real field file is necessary. Mock URLs for pure renderer tests so they never contact S3.

## Done criteria

- [ ] Every image-bearing Content & Calendar model has an appropriate list and/or edit preview as specified.
- [ ] No affected preview method contains inline image sizing.
- [ ] Every rendered image has non-empty alt text, lazy loading, and explicit dimensions.
- [ ] Missing or inaccessible media renders a safe text fallback instead of raising.
- [ ] Focused tests, branding regression tests, `git diff --check`, and `scripts/crabbox-validate.sh ci` pass.
- [ ] No migrations are created.
- [ ] Only in-scope files are modified.
- [ ] Plan 002 is marked Implemented in `advisor-plans/README.md`.

## STOP conditions

- The implementation requires changing a model field, thumbnail generator, upload path, or storage backend.
- A preview requires a network request during test execution.
- Existing dependent-plan work changed a preview method's public contract in a way this plan cannot preserve.
- The full validation fails twice for reasons introduced by these changes.
- Any new migration is generated.

## Maintenance notes

- Future admin media previews must use `admin_thumbnail` and the shared CSS classes.
- Reviewers should scrutinize HTML escaping, storage exception handling, and accidental thumbnail generation in list views.
- Dynamic previews for newly selected autocomplete values are intentionally deferred to plan 008.
