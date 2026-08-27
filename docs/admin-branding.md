# Django admin branding

The Fast & Pray admin uses Django's native `AdminSite`, template override, and staticfiles extension points. It does not depend on an admin-theme package.

## Brand source

The operative source is `/Users/mattash/Projects/bahk_landing/src/styles/global.css`, inspected at landing-repository commit `b56a679` on 2026-08-26. That file identifies itself as the Fast & Pray brand-token source and a mirror of `brand-spec.md`. No separate `brand-spec.md` was present when this theme was implemented.

The admin maps those tokens to Django's semantic variables in `static/admin/css/fastandpray-admin.css`. Dashboard-specific layout and modal styles live in `static/admin/css/fastandpray-analytics.css`.

## Assets

These files are copied byte-for-byte from the landing repository into project-owned static paths:

| Admin asset | Landing source | SHA-256 |
| --- | --- | --- |
| `static/admin/brand/app-icon.png` | `public/app-icon-1024.png` | `ae1965df4ba8f2418d22548d5efa89df97842c4171622728e2ecb99458a68ffc` |
| `static/admin/brand/wordmark.png` | `public/FastandPrayLogo.png` | `0edbf87ac8813b473f24fb4e6a08c5137dd6b01666b4ea22e2d09c6054004fe8` |
| `static/admin/brand/favicon.svg` | `public/favicon.svg` | `2d7a310283d6f9cc753210d83224cd6db6348cb82a5536348884e5831d46203f` |

The project-owned `static/admin/brand/admin-icons.svg` sprite supplies the admin's interface icon system. Section and app icon names are assigned centrally in `bahk/admin_site.py`; templates render them through `templates/admin/includes/icon.html`. Icons are decorative when adjacent to text, use `currentColor`, and never replace accessible labels.

Verify them with:

```bash
shasum -a 256 \
  static/admin/brand/app-icon.png \
  static/admin/brand/wordmark.png \
  static/admin/brand/favicon.svg
```

## Color rules

- Use `#390714` for strong dark surfaces such as the admin header.
- Use `#80273E` for normal links, focus, and routine controls on light surfaces.
- Use `#661D30` for the light-theme accent hover state.
- Reserve `#B5244A` and `#E54670` for emphasis, chart series, or dark-theme accents.
- Do not use `#E54670` as normal-size text on white; it does not meet the intended contrast threshold there.
- Keep the dark maroon wordmark on the login page's permanently light panel.
- Preserve Django's destructive and error semantics instead of recoloring them as brand actions.

Light, dark, and auto values must be updated together. Auto mode needs an explicit `prefers-color-scheme: dark` branch because Django's theme script leaves `data-theme="auto"` on the root element.

## Architecture

- `bahk.admin_config.FastAndPrayAdminConfig` installs `bahk.admin_site.FastAndPrayAdminSite` as Django's default site.
- Existing `admin.site` registrations remain unchanged.
- Dashboard sections and quick actions are derived from `AdminSite.get_app_list()`, after Django has applied model permissions.
- Section, app, model, and action icons come from one SVG sprite and share the same sizing, stroke, and color rules.
- Unknown apps are retained in an Other section so new dependencies cannot disappear from the index.
- `templates/admin/base_site.html` changes only branding/head blocks and preserves Django user tools and theme controls.
- Analytics views merge `self.admin_site.each_context(request)` and render with `TemplateResponse`.

## Synchronizing future brand changes

1. Compare the landing token source with both admin CSS files.
2. If `brand-spec.md` exists and conflicts with `global.css`, stop and resolve which file is authoritative.
3. Copy changed assets byte-for-byte into `static/admin/brand/`.
4. Update the source commit and hashes in this document.
5. Run the targeted tests, `collectstatic --dry-run`, and `scripts/crabbox-validate.sh ci`.
6. Visually verify login, index, change-list, change-form, and both analytics pages at desktop and mobile widths in auto, light, and dark modes.

Figtree is loaded from the same Google Fonts stylesheet as the landing site, with system-font fallbacks. If external font loading becomes unacceptable, self-hosting should be handled as a separate change.
