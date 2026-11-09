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

The regression tests use the Django client so template rendering happens before status
and body assertions. DummyCache prevents cached responses from masking errors.

## Related endpoints and browsable rendering

The same rendered 400 contract covers `/api/fasts/by-date/`,
`/api/fasts/by-feast-date/?date=YYYY-MM-DD`, and `/api/devotionals/`.
Their successful list/pagination shapes stay unchanged. Date validation is also
covered with valid church context. `RelatedChurchListRenderingTests` exercises
actual JSON and HTML rendering, including authenticated selection errors and a
real DRF `SearchFilter` on each of the four list views.

`ChurchListBrowsableAPIRenderer` is explicitly selected on these four list views
because DRF otherwise calls `get_queryset()` to build filter forms even when
there are no filter backends. That repeats church validation outside the view's
exception handling and can turn a controlled 400 into a rendering error.
Per-view opt-in limits this behavior to the affected lists and leaves other
`ChurchContextMixin` consumers, including the retrieve view, on their existing
renderers. It skips only unused filter forms; configured backends still run and
render their controls through DRF. If a backend is added later, DRF's queryset
introspection on error responses needs reassessment. JSON remains first and
HTML remains available.
