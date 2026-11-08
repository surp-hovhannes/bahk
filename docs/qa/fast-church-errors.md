# Legacy fast church errors (#604)

These rules apply to `/api/fast/` and `/api/fasts/`, including browsable HTML
(`Accept: text/html`) and JSON (`Accept: application/json`). They do not change
the separate [public v1 contract](../public-api-contract.md).

Anonymous callers must provide `church_id`. Errors use the existing legacy
400 response shape: a JSON array containing a message (also shown in HTML).

| Input | Status | Message |
| --- | --- | --- |
| Missing or empty `church_id` | 400 | `church_id query parameter is required.` |
| Non-integer `church_id` | 400 | `church_id must be a valid integer.` |
| Integer with no matching church | 400 | `Church with the provided ID does not exist.` |

A valid ID selects that church explicitly, even when multiple churches exist.
No default church is selected for anonymous callers.

Authenticated content uses the profile's selection. An incomplete profile is
assigned the sole church as in #601; zero or multiple churches require selection.
A missing profile also requires selection, even with one church. These errors
return 400 with `{"code":"church_selection_required","detail":"Select a church before continuing."}`.
As before, `/api/fasts/` ignores authenticated `church_id` overrides, while
`/api/fast/` permits explicit selection via `church_id`. The latter resolves
profile context only when the parameter is absent. Explicit IDs receive the
same validation as anonymous IDs.

Focused regression command inside the existing project runtime:

```sh
python manage.py test hub.tests.test_fast_church_errors hub.tests.test_church_selection tests.test_fast_endpoints --noinput --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings
ruff check .
```

The new tests use the Django client so template rendering happens before status
and body assertions. DummyCache prevents cached responses from masking errors.
