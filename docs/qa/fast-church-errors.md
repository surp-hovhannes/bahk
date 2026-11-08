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

## PR #607 review follow-up

The same rendered 400 contract now covers `/api/fasts/by-date/`,
`/api/fasts/by-feast-date/?date=YYYY-MM-DD`, and `/api/devotionals/`.
Their successful list/pagination shapes stay unchanged. Date validation is also
covered with valid church context. `RelatedChurchListRenderingTests` exercises
actual JSON and HTML rendering, including authenticated selection errors and a
real DRF `SearchFilter` on each of the four list views.

Impact inspection found no production `filter_backends` overrides in this
checkout. The shared renderer is explicitly selected on these four list views,
rather than changing global renderer settings or every `ChurchContextMixin`
consumer (which also includes a retrieve view). It skips only unused filter
forms; configured backends still run and render their controls through DRF.
If a backend is added later, DRF's existing queryset introspection on error
responses needs reassessment; this change deliberately does not suppress backend
exceptions globally. JSON remains first and HTML remains available.

The exception nit is addressed: query parameter values are strings, so malformed
integer lookups need `ValueError` handling. Django's out-of-range integer lookup
returns no match, covered by the existing 100-digit ID regression case; it keeps
the unknown-church message rather than claiming a malformed integer.

### Mobile source evidence and limits

Inspected `surp-hovhannes/bahk_react_native` default branch `main` at
[`4d7daa4384906199978b994c478104ed78a8e993`](https://github.com/surp-hovhannes/bahk_react_native/tree/4d7daa4384906199978b994c478104ed78a8e993)
on 2026-11-08. This is source evidence, not a claim about deployed clients.

- `constants/constants.ts` defines fast list and by-date URLs as `/api/fasts/`
  and `/api/fasts/by-date/`; `api/fasts.ts` calls those, passing `church_id`
  (default 1). The inspected fast helpers/constants do not call `/api/fast/`.
- `api/client.ts` attaches an available bearer token even on `public-read`
  requests. Its response interceptor rejects 400 responses; it does not map
  `church_selection_required` to a special action.
- `app/_layout.tsx` mounts `ChurchSelectionGate`. The gate independently checks
  the signed-in profile: a missing church or failed profile load blocks content
  with a selection modal. Saving a church invalidates queries, allowing failed
  content requests to retry. Content may fail before profile resolution.

Thus this source has a profile-based recovery path, rather than endpoint-specific
400 handling. It does not prove authenticated `/api/fast/` callers in released
builds recover correctly. Older clients, timing races, and actual device behavior
remain unverified. Preserve #601: do not restore silent empty content for an
unselected authenticated profile merely to mask those compatibility limits.

### Follow-up validation and rollback

- `uvx --offline ruff check .`: passed using cached Ruff.
- `git diff --check`: passed.
- `python3.11 -m py_compile hub/renderers.py hub/views/fast.py hub/views/devotionals.py hub/views/mixins.py hub/tests/test_fast_church_errors.py`: passed.
- Focused Django tests not run: local Docker daemon is unavailable, local Python
  3.11 has no Django, and the newly warmed Crabbox has no Docker or Python project
  runtime. No heavyweight runtime/dependency installation was attempted.
- Full tests intentionally deferred to CI per this follow-up's scope.

Pending focused command in a compatible runtime:

```sh
python manage.py test hub.tests.test_fast_church_errors hub.tests.test_church_selection hub.tests.test_devotional_list_api --noinput --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings
```

Risk: new rendered regression tests still need execution in CI; future filter
backends may reintroduce DRF error introspection. No schema changes. Rollback:
revert the follow-up commit; this restores the base PR's fast-list-only renderer
and exception tuple without altering #601's selection service.
