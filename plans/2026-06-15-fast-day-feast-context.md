# Plan: Generate FeastContext for Real Commemorations Whose Names Include Fast

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report instead of broadening the fix.
>
> **Drift check (run first)**:
>
> ```bash
> git diff --stat 339416a..HEAD -- hub/views/feasts.py hub/tasks/llm_tasks.py hub/tests/test_feast_views.py tests/tasks/test_llm_tasks.py
> ```
>
> If any in-scope file changed since this plan was written, compare the "Current state" excerpts below against live code before proceeding.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `339416a`, 2026-06-15

## Why this matters

On 2026-06-15, `/api/feasts/` returns Feast `282` named `Fast day, Saints Epiphanius Bishop of Cyprus, Babylas the Patriarch, and his three disciples`, designation `Martyrs`, icon `627`, but empty `text` and `short_text`. Prod data shows Day `846` has `fast_id=146` (`Fast of our Holy Father St Gregory the Illuminator`) and zero `hub_feastcontext` rows for Feast `282`. The API view suppresses generation for any feast whose name contains `"Fast"`, even when the feast is a real saint/feast commemoration; the worker only skips generic fast days by designation. Align the API enqueue rule with the worker so non-generic commemorations get FeastContext.

## Current State

- `hub/views/feasts.py` serves `/api/feasts/`; it already calls `get_or_create_feast_for_date(date_obj, church, check_fast=False)` so a Day with a Fast can still show a Feast.
- `hub/tasks/llm_tasks.py` owns FeastContext generation and skips only `Feast.Designation.FAST`.
- `hub/tests/test_feast_views.py` contains the mounted `/api/feasts/` tests and already patches `hub.views.feasts.generate_feast_context_task.delay`.
- `tests/tasks/test_llm_tasks.py` has task tests, but currently only covers reading-context generation.

Relevant excerpts at commit `339416a`:

```python
# hub/views/feasts.py:85-94
feast_obj, _, _ = get_or_create_feast_for_date(date_obj, church, check_fast=False)
day = Day.objects.get(date=date_obj, church=church)
feast = feast_obj if feast_obj else day.feasts.first()
```

```python
# hub/views/feasts.py:120-133
active_context = feast.active_context
should_trigger_generation = True

# Don't trigger context generation if feast name includes "Fast"
if "Fast" in feast.name:
    should_trigger_generation = False

if active_context is None:
    if should_trigger_generation:
        generate_feast_context_task.delay(feast.id)
```

```python
# hub/tasks/llm_tasks.py:238-241
# Skip context generation for generic fast days - they are never displayed
if feast.designation == Feast.Designation.FAST:
    logger.info("Feast %s is a generic fast day, skipping context generation.", feast_id)
    return
```

```python
# hub/models.py:715-739
class Designation(models.TextChoices):
    MARTYRS = ('Martyrs', 'Martyrs')
    FAST = ('Fast', 'Fast')
```

```python
# hub/tests/test_feast_views.py:212-253
@patch("hub.views.feasts.generate_feast_context_task.delay")
@patch("hub.views.feasts.get_or_create_feast_for_date")
...
mock_get_or_create.assert_called_once_with(
    self.test_date,
    self.church,
    check_fast=False,
)
```

Repo command notes:

- From the local host, run Django commands through Docker:
  `docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py <command>`
- Inside Crabbox/app runtime, run Django directly:
  `python manage.py <command>`
- Full repo validation should use Crabbox:
  `scripts/crabbox-box.sh warm` then `scripts/crabbox-validate.sh ci`
- Do not rely on `pytest`, `npm test`, or baseline `ruff format --check`; repo notes say those are broken/noisy.

## Scope

**In scope**:

- `hub/views/feasts.py`
- `hub/tasks/llm_tasks.py`
- `hub/tests/test_feast_views.py`
- `tests/tasks/test_llm_tasks.py`

**Out of scope**:

- Migrations, schema, data-loss paths, and data backfills.
- Scraping or `get_or_create_feast_for_date` redesign.
- Icon matching, icon cache behavior, and icon API shape.
- Feast API cache invalidation strategy. Existing cached empty responses may live until normal TTL or existing invalidation.
- UI/client changes.
- Broad LLM prompt/content changes beyond preserving current FeastContext generation.

## Implementation Approach

### Step 1: Centralize FeastContext eligibility

In `hub/tasks/llm_tasks.py`, add a small helper near the feast-context helpers:

```python
def is_feast_context_generation_eligible(feast: Feast) -> bool:
    """Return whether FeastContext generation should run for this feast."""
    return feast.designation != Feast.Designation.FAST
```

Then update `generate_feast_context_task` to use that helper instead of comparing `feast.designation` inline. Keep the existing log message and behavior for generic fast days.

This helper deliberately uses designation, not the feast name. A name like `Fast day, Saints ...` with designation `Martyrs` must be eligible. A generic fast day with designation `Fast` must remain ineligible.

**Verify**:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test tests.tasks.test_llm_tasks --settings=tests.test_settings
```

Expected: existing task tests pass, or if the container is unavailable locally, record that and run the Crabbox validation gate at the end.

### Step 2: Use the same eligibility rule in the API view

In `hub/views/feasts.py`, replace the substring gate:

```python
should_trigger_generation = True
if "Fast" in feast.name:
    should_trigger_generation = False
```

with a call to the helper from Step 1, for example:

```python
from hub.tasks.llm_tasks import is_feast_context_generation_eligible

...
should_trigger_generation = is_feast_context_generation_eligible(feast)
```

Keep the existing response shape and existing behavior of returning blank `text`/`short_text` on the first request while the Celery task is enqueued.

**Verify**:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test hub.tests.test_feast_views --settings=tests.test_settings
```

Expected: view tests pass.

### Step 3: Add regression tests

Add focused tests that prove the desired eligibility boundary.

In `tests/tasks/test_llm_tasks.py`:

- Import `Feast`, `FeastContext`, and the new `is_feast_context_generation_eligible` helper.
- Add a small test class or method that creates a Feast named `Fast day, Saints Epiphanius Bishop of Cyprus, Babylas the Patriarch, and his three disciples` with designation `Feast.Designation.MARTYRS` and asserts the helper returns `True`.
- Add a second test creating a Feast with designation `Feast.Designation.FAST` and assert the helper returns `False`.

In `hub/tests/test_feast_views.py`, add tests near `FeastAPIRouteTests`:

- `test_api_route_enqueues_context_for_fast_named_real_commemoration`
  - Create a Day with a real `Fast` attached if convenient, or at minimum create a Feast whose name starts with `Fast day, Saints ...`.
  - Set feast designation to `Feast.Designation.MARTYRS`.
  - Patch `hub.views.feasts.get_or_create_feast_for_date` to return that feast.
  - Patch `hub.views.feasts.generate_feast_context_task.delay`.
  - Call `self.client.get("/api/feasts/", {"date": self.date_str})`.
  - Assert response is `200`, `designation == Feast.Designation.MARTYRS`, `text == ""`, `short_text == ""`, and `mock_generate_context.assert_called_once_with(feast.id)`.
- `test_api_route_does_not_enqueue_context_for_generic_fast_designation`
  - Use a Feast with designation `Feast.Designation.FAST`.
  - Assert the API still returns the feast payload but `mock_generate_context.assert_not_called()`.

Use existing signal patches in `_create_feast` or the nearby test pattern so icon/designation background tasks do not leak into assertions.

**Verify**:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test hub.tests.test_feast_views tests.tasks.test_llm_tasks --settings=tests.test_settings
```

Expected: all targeted tests pass, including the new regression tests.

## Crabbox Validation

After the targeted tests pass or if local Docker is unavailable, run the repo's required CI-style gate:

```bash
scripts/crabbox-box.sh warm
scripts/crabbox-validate.sh ci
```

Expected: exit 0. Do not commit `.crabbox/` runtime state.

## Done Criteria

- [ ] API view no longer suppresses FeastContext enqueue solely because `feast.name` contains `"Fast"`.
- [ ] API view and worker use the same designation-based eligibility rule.
- [ ] A `Martyrs` feast named `Fast day, Saints ...` enqueues `generate_feast_context_task.delay(feast.id)` when no active context exists.
- [ ] A generic feast with `designation == Feast.Designation.FAST` still does not enqueue context generation.
- [ ] No migrations, schema changes, data backfills, cache-invalidation changes, scraping changes, icon matching changes, or UI changes are included.
- [ ] Targeted Django tests pass.
- [ ] `scripts/crabbox-validate.sh ci` passes in Crabbox.

## STOP Conditions

Stop and report if:

- The live code no longer has the view-level `"Fast" in feast.name` gate or the worker-level `designation == Feast.Designation.FAST` skip shown above.
- The fix appears to require changing `get_or_create_feast_for_date`, migrations, schema, scrape logic, icon matching, API response shape, or UI.
- Importing the helper from `hub.tasks.llm_tasks` into `hub/views/feasts.py` creates an import cycle; in that case, do not improvise a larger refactor. Report the cycle and propose moving the helper to a tiny neutral module such as `hub/services/feast_context_eligibility.py`.
- Targeted tests fail twice after reasonable fixes.

## Maintenance Notes

Reviewers should look for any reintroduction of name-based fast filtering. In this domain, `"Fast day, ..."` can be the display name for a real saint/feast commemoration, while `Feast.Designation.FAST` is the durable marker for generic fast days. This plan intentionally leaves cache invalidation and existing cached empty responses alone; after deployment, already cached responses may remain empty until normal TTL or an existing Feast/FeastContext invalidation event.
