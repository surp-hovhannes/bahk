# Plan 009: Accept ISO calendar dates on legacy fast endpoints

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If a STOP condition occurs, stop and report — do not improvise. When done, update the status row for this plan in `advisor-plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 7289ba8..HEAD -- hub/views/fast.py tests/integration/test_endpoints.py`
> If either in-scope file changed since this plan was written, compare the current state to the excerpts below. Treat a mismatch as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: Low
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `7289ba8`, 2026-08-31

## Why this matters

Sentry issue #487 records the production request date `2026-08-21`, which the legacy fast-on-date endpoints reject because they parse only compact `YYYYMMDD`. The parser logs an error and returns `None`; the subsequent fast query uses that value rather than returning a client-visible date validation error. Accepting the ISO-8601 calendar format used by the client resolves the observed request without breaking callers that still send compact dates.

This plan intentionally preserves existing response behavior for malformed dates and changes only accepted input formats. It does not alter endpoint paths, authentication, serialization, or the fast-query behavior.

## Current state

- `hub/views/fast.py` implements both legacy fast-on-date views and their shared date parser.
- `hub/urls.py:83-85` exposes `FastOnDate` at `user/fasts/` and `FastOnDateWithoutUser` at `fast/`; the project mounts these routes below both `/hub/` and `/api/` (`bahk/urls.py:108-145`).
- `tests/integration/test_endpoints.py` contains endpoint-level tests for the authenticated/anonymous `FastOnDate` behavior and is the established test location.

`hub/views/fast.py:900-954` currently routes both views through the same parser:

```python
def _get_fast_for_user_on_date(request):
    date_str = request.query_params.get("date")
    if date_str is None:
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)
    return _get_user_fast_on_date(user, date)


def _get_fast_on_date(request):
    date_str = request.query_params.get("date")
    if date_str is None:
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)
    # resolves church, then queries Fast.days__date=date


def _parse_date_str(date_str):
    """Parses a date string in the format yyyymmdd into a date object."""
    try:
        date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        logging.error("Date string %s did not follow the expected format yyyymmdd. Returning None.", date_str)
        return None
    return date
```

`tests/integration/test_endpoints.py:99-104` establishes the compact-format endpoint contract:

```python
def test_fast_on_date_with_query_params_countdown(self):
    from django.utils import timezone
    today = timezone.localdate()
    date_str = today.strftime("%Y%m%d")
    self._assert_fast_on_date_countdown(query_params=f"?date={date_str}")
```

Match the test class's `TestCase`, `TestDataFactory`, `reverse`, and `_assert_fast_on_date_countdown` helper conventions. Do not introduce an alternate parser outside `hub/views/fast.py`; both endpoint variants must continue to share one parser.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused regression test | `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.integration.test_endpoints.FastOnDateEndpointTests --exclude-tag=performance --settings=tests.test_settings` | Exit 0; all `FastOnDateEndpointTests` pass, including new ISO test(s). |
| Full Django validation | `scripts/crabbox-box.sh warm && scripts/crabbox-validate.sh test` | Exit 0; the Crabbox Django test job passes. |

Run the Docker command from the host checkout. Use the warmed Crabbox validation command only after the focused test passes.

## Scope

**In scope** (the only application files to modify):

- `hub/views/fast.py` — broaden `_parse_date_str` to recognize exactly compact `YYYYMMDD` and ISO `YYYY-MM-DD` calendar dates; update its docstring and error message to describe both accepted formats.
- `tests/integration/test_endpoints.py` — add endpoint regression coverage proving an ISO date selects the same fast/countdown as the current compact date test.

**Out of scope**:

- `hub/urls.py` and `bahk/urls.py` — do not rename or remove the legacy routes.
- Client/mobile code — the server must remain compatible with deployed compact-date clients.
- Response shape and malformed-date semantics — do not turn malformed dates into a 400 as part of this incident fix.
- Other date-query endpoints — no evidence shows they receive the bad input.
- Schema or migration changes.

## Git workflow

- Branch: `advisor/009-accept-iso-fast-date`.
- Keep the patch to one logical commit after tests pass; use the repository's existing concise imperative commit style, for example `Accept ISO dates on fast endpoint`.
- Do not push or open a pull request unless the operator instructs it.

## Steps

### Step 1: Add explicit dual-format parsing

In `hub/views/fast.py`, update `_parse_date_str` so it tries the two supported, exact layouts in a deterministic order:

1. Existing compact `%Y%m%d` for backwards compatibility.
2. ISO calendar `%Y-%m-%d` for the Sentry-reported client input.

Return a `datetime.date` for either valid format. If neither parse succeeds, retain the existing `None` return. Update the docstring and error log to list both formats. Do not use a permissive parser that accepts timestamps, locale-specific dates, partial dates, or whitespace-padded inputs; this endpoint expects a calendar date only.

**Verify**: `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.integration.test_endpoints.FastOnDateEndpointTests --exclude-tag=performance --settings=tests.test_settings` → exit 0 before adding the regression test confirms the localized implementation does not break existing compact-date coverage.

### Step 2: Lock the ISO endpoint regression in tests

In `tests/integration/test_endpoints.py`, add a test in `FastOnDateEndpointTests` adjacent to `test_fast_on_date_with_query_params_countdown`. Use `timezone.localdate().isoformat()` and the existing `_assert_fast_on_date_countdown` helper so the test executes the actual URL, query parameter parsing, church resolution, fast query, and response serialization. Keep the existing compact-date test unchanged; it is the backward-compatibility contract.

Add a direct unit-level malformed-input assertion only if this test module already has a suitable way to invoke `_parse_date_str` without bypassing endpoint behavior. Otherwise, do not enlarge scope merely to test unchanged malformed-input behavior.

**Verify**: `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.integration.test_endpoints.FastOnDateEndpointTests --exclude-tag=performance --settings=tests.test_settings` → exit 0; both compact `YYYYMMDD` and ISO `YYYY-MM-DD` date requests return the expected countdown.

### Step 3: Run the project Django suite remotely

After the focused regression test passes, warm/reuse Crabbox and run the repository Django test validation.

**Verify**: `scripts/crabbox-box.sh warm && scripts/crabbox-validate.sh test` → exit 0; the remote Django test job completes successfully.

## Test plan

- Preserve `test_fast_on_date_with_query_params_countdown` as the compact-date compatibility test.
- Add an adjacent test that sends `?date=<timezone.localdate().isoformat()>` through the real test client and asserts the same expected countdown.
- The existing default-date and culmination-feast tests remain unchanged, protecting the no-query-param branch and related date-dependent behavior.
- Run the focused Docker command and then `scripts/crabbox-validate.sh test` against the warmed Crabbox box.

## Done criteria

- [ ] `_parse_date_str` accepts only valid `YYYYMMDD` and `YYYY-MM-DD` calendar-date values and returns `datetime.date` for both.
- [ ] An ISO date request reaches `FastOnDate` and returns the expected fast countdown.
- [ ] The existing compact date request remains covered and passes unchanged.
- [ ] Invalid-date handling remains the existing `None` path; no public response contract changes.
- [ ] The focused Docker test command exits 0.
- [ ] `scripts/crabbox-box.sh warm && scripts/crabbox-validate.sh test` exits 0.
- [ ] No files outside `hub/views/fast.py`, `tests/integration/test_endpoints.py`, and the advisor-plan status update are changed.

## STOP conditions

Stop and report instead of improvising if:

- The in-scope parser or endpoint tests no longer match the excerpts above.
- Supporting ISO dates requires changing a frontend caller, route, serializer, response status, or database schema.
- The Sentry event is traced to a different endpoint than `FastOnDate` / `FastOnDateWithoutUser`.
- A valid ISO input still fails after `_parse_date_str` returns a `datetime.date`, indicating a separate fast-query/data issue.
- The focused test fails twice after a reasonable localized correction.

## Maintenance notes

The two legacy routes deliberately share `_parse_date_str`; future date-format changes must preserve that centralization so authenticated and anonymous requests cannot drift. A reviewer should reject permissive parsing that accepts timestamps or silently normalizes malformed input. The API's longer-term date-parameter contract and validation behavior belong to public-API issue #496, not this incident fix.
