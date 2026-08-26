# Brand and improve the Django admin

- **Status:** Implemented
- **Priority:** P1
- **Category:** Tech debt / product direction
- **Effort:** L (multi-day)
- **Risk:** Medium
- **Planned at:** `b2c57cf`
- **Target branch:** `codex/admin-branding`
- **Suggested commit:** `feat: brand Django admin`
- **Dependencies:** None

## Summary

Give the Django 5.2 admin a cohesive Fast & Pray identity and improve its navigation, theme behavior, responsiveness, and accessibility without introducing a third-party admin theme or changing data behavior.

Implementation is complete on `codex/admin-branding`. The exact local CI-equivalent gates pass in the rebuilt Django 5.2.17 container (`ruff check .` and 1,343 eligible Django tests, with 2 existing skips). Static collection, focused template/static checks, and asset-hash verification also pass. The responsive dashboard and icon-system follow-up were reviewed in the local browser and approved on 2026-08-26.

The implementation should use Django's supported `AdminSite`, `AdminConfig.default_site`, and template override mechanisms. Existing `admin.site` registrations continue to work, while a custom site supplies global labels and permission-filtered navigation sections. Shared CSS and canonical brand assets replace page-specific inline styling. The two analytics dashboards are brought back into the standard admin context so the header, user tools, breadcrumbs, and theme controls behave consistently.

This is an internal operations interface, not a replacement for product-facing workflows. Keep the customization native, restrained, and maintainable.

## Why this work is needed

The current admin is functional, but it presents 36 models in a flat, technically named hierarchy. The home page, event change list, and analytics dashboards use separate inline styles and visual conventions. The custom analytics responses do not merge the active `AdminSite` context, which is why the standard user tools and theme behavior are incomplete there. Dark mode exposes low-contrast text and controls on hard-coded white cards.

The live visual audit also found:

- no Fast & Pray identity in the global admin shell or login page;
- event navigation represented with one-off blue/purple links and emoji;
- custom dashboard page titles duplicated inside the Django page title region;
- unlabeled date-range selects;
- clickable metric cards implemented as mouse-only `<div onclick>` elements;
- chart canvases without accessible names;
- dashboard colors that do not adapt when the Django theme changes; and
- responsive stacking that works structurally but retains the dark-mode contrast failures.

## Brand source of truth

Use `/Users/mattash/Projects/bahk_landing/src/styles/global.css` as the operative brand guide. Its opening comment identifies it as the Fast & Pray brand-token source and a mirror of `brand-spec.md`; no separate `brand-spec.md` currently exists in the landing repository or its history.

Canonical values to mirror:

| Role | Value | Admin use |
| --- | --- | --- |
| Background / surface | `#FFFFFF` | Light page and card surfaces |
| Foreground | `#3B0714` | Primary light-theme text |
| Muted foreground | `#5B2030` | Secondary light-theme text |
| Primary | `#390714` | Header, strong dark fills, footer-like regions |
| Accent | `#80273E` | Light-theme links, focus, buttons, active states |
| Accent hover | `#661D30` | Hover/pressed state on light surfaces |
| Accent alternate | `#B5244A` | Larger emphasis and restrained decorative use |
| Accent bright | `#E54670` | Dark-theme accent and limited flourish only |
| Typeface | Figtree 400/600/700 | Admin typography, with system fallbacks |
| Radii | 4/6/8/16 px | Controls, cards, panels, large surfaces |
| Focus ring | 2 px accent | All interactive controls |
| CTA gradient | `#B5244A` → `#E54670` | One decisive flourish, not routine UI chrome |

Contrast guardrails established during the audit:

- `#80273E` on white is suitable for normal light-theme link text.
- `#E54670` on white is not suitable for normal-size text and must not become the default light-theme link or small-button color.
- White on `#390714` is suitable for the global header.
- `#E54670` on the proposed near-black dark surface is acceptable, but the finished controls still require browser verification.

Copy these source assets byte-for-byte into project-owned static paths so production does not depend on a sibling checkout:

| Source | Destination | SHA-256 |
| --- | --- | --- |
| `/Users/mattash/Projects/bahk_landing/public/app-icon-1024.png` | `static/admin/brand/app-icon.png` | `ae1965df4ba8f2418d22548d5efa89df97842c4171622728e2ecb99458a68ffc` |
| `/Users/mattash/Projects/bahk_landing/public/FastandPrayLogo.png` | `static/admin/brand/wordmark.png` | `0edbf87ac8813b473f24fb4e6a08c5137dd6b01666b4ea22e2d09c6054004fe8` |
| `/Users/mattash/Projects/bahk_landing/public/favicon.svg` | `static/admin/brand/favicon.svg` | `2d7a310283d6f9cc753210d83224cd6db6348cb82a5536348884e5831d46203f` |

If a real `brand-spec.md` appears and conflicts with these tokens or assets, stop and reconcile the sources before implementation.

## Current implementation evidence

### Admin wiring

`bahk/settings.py` currently installs the stock site and already makes project templates discoverable:

```python
INSTALLED_APPS = [
    'django.contrib.admin',
    # ...
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        # ...
    },
]
```

Static files currently define only the deployment URL and collection root:

```python
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
```

`bahk/urls.py` mounts the default site:

```python
path('admin/', admin.site.urls),
```

Existing apps register models against `admin.site`, including 15 decorator registrations in `hub/admin.py`. Re-registering every model on a new site would be error-prone; configuring Django's default site class preserves these registrations.

### Existing overrides and dashboards

`templates/admin/index.html` intentionally extends the same logical template name:

```django
{% extends "admin/index.html" %}
```

This is a supported Django override pattern because the project template is found before the app template and the parent lookup continues to the next matching template. Keep this pattern where it reduces copying of Django's upstream markup.

The active home-page override contains inline blue/purple styles and emoji. `templates/admin/events/index_with_analytics.html` duplicates the layout but is dead code: `events/admin.py` defines an `EventsAdminSite` class that changes `admin.site.index_template`, yet that class is never instantiated.

The analytics views currently render an isolated context:

```python
return render(request, 'admin/events/analytics.html', context)
```

and:

```python
return render(request, 'admin/events/app_analytics.html', context)
```

Neither context is merged with `self.admin_site.each_context(request)`. The templates also contain hundreds of lines of hard-coded inline CSS, duplicated `<h1>` elements, unlabeled selects, pointer-only metric cards, and fixed Chart.js colors.

## Proposed design

### 1. Install a branded default `AdminSite`

Create `bahk/admin_site.py` with `FastAndPrayAdminSite(AdminSite)`:

```python
class FastAndPrayAdminSite(AdminSite):
    site_header = "Fast & Pray Admin"
    site_title = "Fast & Pray Admin"
    index_title = "Operations"
    index_template = "admin/index.html"
```

Override `index()` only to add a permission-safe `admin_sections` value derived from `super().get_app_list(request)`. Do not inspect the registry directly and do not hard-code model URLs. `get_app_list()` has already applied model permissions and generated the correct add/change URLs.

Group visible apps in this order:

1. **Content & Calendar:** `hub`, `prayers`, `learning_resources`, `icons`
2. **Engagement & Messaging:** `events`, `notifications`
3. **Operations:** `app_management`
4. **Administration:** `auth`, `django_celery_beat`, `taggit`
5. **Other:** every visible app not listed above, sorted by display name

Never hide a newly installed or third-party app merely because it is not in the map. Omit empty sections.

Create `bahk/admin_config.py`:

```python
from django.contrib.admin.apps import AdminConfig


class FastAndPrayAdminConfig(AdminConfig):
    default_site = "bahk.admin_site.FastAndPrayAdminSite"
```

Then replace `'django.contrib.admin'` in `INSTALLED_APPS` with `'bahk.admin_config.FastAndPrayAdminConfig'`. Leave `bahk/urls.py` and every current `admin.site` registration unchanged.

### 2. Add project-owned assets and global admin styling

Add the three verified brand assets under `static/admin/brand/` and configure:

```python
STATICFILES_DIRS = [BASE_DIR / 'static']
```

Create:

- `static/admin/css/fastandpray-admin.css` for the native admin shell, typography, semantic colors, focus states, cards, grouped index, change-list action links, login treatment, and responsive rules;
- `static/admin/css/fastandpray-analytics.css` for dashboard grids, metrics, chart panels, modal behavior, and dashboard-specific responsive rules.

Map brand values onto Django's existing CSS custom properties rather than restyling every upstream selector. Define explicit values for:

- `:root` and `html[data-theme="light"]`;
- `html[data-theme="dark"]`; and
- `@media (prefers-color-scheme: dark) { html[data-theme="auto"] { ... } }`.

At minimum cover Django variables such as `--primary`, `--secondary`, `--accent`, `--header-bg`, `--header-color`, `--body-bg`, `--body-fg`, `--link-fg`, `--link-hover-color`, `--breadcrumbs-bg`, `--selected-bg`, `--button-bg`, and `--button-hover-bg`. Preserve upstream semantics for destructive buttons and error states.

Use the landing site's Figtree stylesheet URL in the global template with system-font fallbacks. The UI must remain fully usable if the external font is blocked; do not self-host or add a package in this change.

### 3. Override the global shell and login presentation

Create `templates/admin/base_site.html`, extending Django's same-named template. Load `static` and `i18n`, then:

- add the favicon, Figtree stylesheet, and `fastandpray-admin.css` in the appropriate head/style blocks;
- replace only the branding block with the app icon and text `Fast & Pray Admin`;
- use an empty `alt` on the icon because the adjacent text supplies the name;
- preserve Django's anonymous-user theme-toggle include exactly; and
- do not replace the user-tools, navigation, messages, breadcrumb, or content markup.

Create `templates/admin/login.html`, also extending the same-named upstream template, and add the full wordmark to a light branded panel. Ensure the dark maroon wordmark always sits on a light surface in light, dark, and auto modes.

Reference documentation:

- Django 5.2 template overrides: <https://docs.djangoproject.com/en/5.2/howto/overriding-templates/>
- Django 5.2 `AdminSite` and site customization: <https://docs.djangoproject.com/en/5.2/ref/contrib/admin/>

### 4. Rebuild the admin index around operational sections

Refactor `templates/admin/index.html` to render `admin_sections` as semantic sections in a responsive grid. Within each app, preserve Django's model names and generated add/change URLs. Do not infer capabilities in the template; render only permission-filtered values supplied by the site.

Add a compact quick-actions region for:

- Import Prayer Sets
- User Engagement
- App Analytics

Resolve each link using named admin URLs and show it only when the corresponding permission or app/model entry is available. Do not hard-code `/admin/...` paths.

Remove inline styles and emoji. Use plain, descriptive text and shared CSS classes. Keep recent actions when the user can see them.

### 5. Restore complete admin context to analytics views

In `events/admin.py`, update both custom dashboard views to use `TemplateResponse` and merge:

```python
{
    **self.admin_site.each_context(request),
    # existing dashboard context
}
```

Also supply the applicable `opts` and `has_view_permission` values expected by admin templates. Preserve the current permission checks, queries, serialized data, endpoints, and response status behavior.

Remove the unused `analytics_link()` helper and uninstantiated `EventsAdminSite` class after confirming no imports or settings reference them. Delete `templates/admin/events/index_with_analytics.html` in the same commit.

### 6. Make the analytics pages accessible and theme-aware

Refactor both:

- `templates/admin/events/analytics.html`
- `templates/admin/events/app_analytics.html`

Required changes:

- load `fastandpray-analytics.css` through a template block rather than embedding page-length CSS;
- remove the duplicate inner `<h1>` and use the Django-provided page title;
- add a visible `<label>` tied to each date-range `<select>`;
- render interactive metric cards as `<button type="button">`, keeping their modal or drill-down behavior and adding visible keyboard focus;
- give every chart canvas a concise accessible name and nearby explanatory text;
- replace emoji-only headings with descriptive text;
- replace inline style strings created by JavaScript with state classes;
- source Chart.js series, tick, grid, tooltip, and legend colors from centralized brand/theme values; and
- listen for the Django theme control changing and update or rebuild charts so toggling light/dark/auto does not leave unreadable axes or legends.

Do not expand this task into new analytics, query optimization, or a charting-library migration. Full data tables for every chart are a valuable follow-up but may be deferred if the concise names and existing numeric summaries make this increment too large.

### 7. Normalize event change-list actions

Update `templates/admin/events/event/change_list.html` so User Engagement and App Analytics use the shared branded action style. Remove inline blue/purple declarations and emoji while preserving URLs, permissions, and placement in `object-tools-items`.

### 8. Document ongoing brand maintenance

Add `docs/admin-branding.md` containing:

- the landing token source and the date/commit used during implementation;
- the copied asset sources and SHA-256 values;
- the light/dark color-role rules and contrast cautions above;
- where global and analytics CSS live;
- the supported Django template override points; and
- the procedure for intentionally resynchronizing assets or tokens.

This prevents a later landing-page refresh from silently drifting the internal admin or encouraging ad hoc inline colors.

## File-by-file change list

### Create

- `bahk/admin_site.py`
- `bahk/admin_config.py`
- `templates/admin/base_site.html`
- `templates/admin/login.html`
- `static/admin/brand/app-icon.png`
- `static/admin/brand/wordmark.png`
- `static/admin/brand/favicon.svg`
- `static/admin/css/fastandpray-admin.css`
- `static/admin/css/fastandpray-analytics.css`
- `tests/unit/test_admin_branding.py`
- `docs/admin-branding.md`

### Modify

- `bahk/settings.py`
- `templates/admin/index.html`
- `templates/admin/events/event/change_list.html`
- `templates/admin/events/analytics.html`
- `templates/admin/events/app_analytics.html`
- `events/admin.py`
- `events/tests/test_admin_dashboards.py`

### Delete after reference check

- `templates/admin/events/index_with_analytics.html`

## Test plan

### Automated coverage

Add `tests/unit/test_admin_branding.py` with tests that verify:

- `admin.site` is an instance of `FastAndPrayAdminSite`;
- `site_header`, `site_title`, and `index_title` have the expected values;
- all three brand assets and both CSS files are discoverable with Django staticfiles finders;
- an authenticated staff response includes the branded stylesheet, site name, section order, and permitted quick actions;
- a staff user without model permissions does not receive model or quick-action links they cannot use;
- unknown visible apps are retained in the Other section; and
- empty sections are omitted.

Extend `events/tests/test_admin_dashboards.py` to verify:

- existing staff-only permission behavior remains unchanged;
- both dashboard responses receive normal site context and render the global brand shell;
- each page has one content heading rather than a duplicated title;
- each date select has an associated visible label;
- metric triggers are buttons rather than pointer-only divs;
- canvases have accessible names; and
- the shared analytics stylesheet is present.

Prefer semantic assertions over snapshots of all HTML or CSS. Do not weaken existing analytics value assertions.

### Targeted local checks

Run from the host, per this repository's Docker convention:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.unit.test_admin_branding events.tests.test_admin_dashboards --noinput --settings=tests.test_settings
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py collectstatic --dry-run --noinput --verbosity 0
git diff --check
```

When already inside Crabbox or the application container, run the `python manage.py ...` forms directly rather than nesting `docker exec`.

### Repository validation

The repository's baseline `ruff format --check`, direct `pytest`, and placeholder `npm test` are not authoritative here. Warm and validate with the project scripts:

```bash
scripts/crabbox-box.sh warm
scripts/crabbox-validate.sh ci
```

Do not commit `.crabbox/` runtime state.

### Focused static checks

Confirm the implementation does not reintroduce inline presentation in the four customized surfaces:

```bash
rg -n 'style=|<style' \
  templates/admin/index.html \
  templates/admin/events/analytics.html \
  templates/admin/events/app_analytics.html \
  templates/admin/events/event/change_list.html
```

Any remaining inline style must be justified in `docs/admin-branding.md`; the expected result is no matches.

Confirm the copied asset bytes:

```bash
shasum -a 256 \
  static/admin/brand/app-icon.png \
  static/admin/brand/wordmark.png \
  static/admin/brand/favicon.svg
```

## Visual QA matrix

Use an authenticated local or Crabbox admin session after automated checks pass. Test at approximately 1497×900 and 390×844 in each of auto, light, and dark theme modes.

Review these surfaces:

1. Login
2. Admin home
3. Representative model change list
4. Representative model change form
5. User Engagement analytics
6. App Analytics

For each surface verify:

- icon, wordmark, text, and favicon are sharp and not distorted;
- header/user tools/theme control remain visible and functional;
- text and controls remain readable in all themes;
- selected, hover, focus, disabled, error, and destructive states remain distinguishable;
- no content overflows the mobile viewport;
- model rows, filters, actions, pagination, and form controls retain native behavior; and
- charts, ticks, grids, legends, and tooltips update after an in-session theme toggle.

Keyboard-check the theme toggle, quick actions, dashboard date select, every metric button, and modal close control. Confirm the modal returns focus to its trigger.

Restore the original theme and viewport at the end of QA.

## Done criteria

- The global admin shell and login page visibly use the canonical Fast & Pray icon, wordmark, palette, and Figtree typography.
- Branding is implemented through native Django site/template/static mechanisms with no third-party theme dependency.
- All existing `admin.site` registrations remain active.
- The home page groups every permitted app into the defined operational sections, including a safe fallback for unknown apps.
- Users never see model or quick-action links beyond their permissions.
- Both analytics views render the complete admin shell and theme controls.
- Light, dark, and auto modes remain readable at desktop and mobile widths.
- Dashboard controls are labeled and keyboard-operable; metric cards are buttons; chart canvases have accessible names.
- Inline page styling is removed from all in-scope custom admin templates.
- Static assets collect successfully and their hashes match the documented source files.
- Targeted Django tests, `git diff --check`, and `scripts/crabbox-validate.sh ci` pass.
- No migration, schema, model-data, or analytics-query changes are present.
- `docs/admin-branding.md` documents the source of truth and maintenance procedure.

## STOP conditions

Stop and request direction if any of the following occurs:

- a model is no longer registered after enabling the custom default site;
- permission-safe sectioning cannot be derived from `get_app_list()` without duplicating Django's authorization logic;
- a dashboard query, endpoint, or serialized-data contract would need to change to complete the UI work;
- a migration or schema change becomes necessary;
- a new `brand-spec.md` conflicts with `bahk_landing/src/styles/global.css`;
- the supplied assets fail the documented hashes or are unsuitable on the required surfaces;
- achieving the design requires replacing Django admin wholesale or adding a third-party theme;
- the baseline Crabbox suite fails for a reason introduced by the branch; or
- production deployment or production data access becomes necessary.

## Out of scope

- Database migrations, schema changes, and data repair
- Model-label, `verbose_name`, `__str__`, field-layout, autocomplete, and list-density changes
- Analytics query changes, new metrics, or chart-library replacement
- Full redesigns of notification, hub, import, or other one-off admin pages not listed above
- Replacing Django admin with a staff-facing product application
- Adding Jazzmin, Unfold, Grappelli, or another admin theme dependency
- Deployment, production writes, or changes to production authentication
- Changing the public landing site's brand files

## Follow-up candidates

After this foundation is stable, evaluate separately:

- model-specific `list_display`, `list_filter`, `search_fields`, autocomplete, and fieldset improvements based on staff workflows;
- friendly model/app labels where technical names impede comprehension;
- accessible data tables or downloadable CSV equivalents for every chart;
- restyling remaining one-off admin pages using the documented shared primitives; and
- self-hosting Figtree if external-font privacy or availability becomes a requirement.

Each follow-up should be based on observed admin tasks, not cosmetic breadth alone.
