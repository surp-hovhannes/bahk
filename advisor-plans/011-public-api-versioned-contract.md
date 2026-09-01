# Plan 011: Establish the v1 public API contract before exposing endpoints

> **Executor instructions**: This plan establishes the contract and versioning boundary for public consumers. It must land before public serializers (#497), validation/error normalization (#496), public endpoint publication (#494), throttling (#498), the calendar endpoint (#499), and public documentation (#500). Keep `/docs/` in its current Coming soon state throughout this plan.
>
> **Drift check**: `git diff --stat 7289ba8..HEAD -- bahk/urls.py hub/urls.py bahk/views.py templates/api_docs.html tests/unit/test_api_site.py docs/`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: Medium
- **Depends on**: none
- **Category**: API contract
- **Planned at**: commit `7289ba8`, 2026-09-01
- **Issue**: https://github.com/surp-hovhannes/bahk/issues/495

## Decision to implement

Create a new, additive public API contract at `/api/v1/`. Existing `/api/` routes remain internal/legacy implementation routes and are not advertised, versioned, or given a public compatibility promise. Do not alias `/api/v1/` to `hub.urls`; that would accidentally expose authenticated, mutable, legacy, and presentation-oriented routes.

V1 is anonymous, read-only, and limited initially to Churches, Readings, Fasts, and Feasts. It does not become publicly documented or feature-complete in this plan: each resource remains unavailable from v1 until #494, #497, #496, and #498 provide its stable serializer, validation, and traffic controls. The version prefix and contract document make that boundary explicit without prematurely publishing unstable responses.

## Current state

- `bahk/urls.py:137-145` mounts mixed client/internal applications directly beneath `/api/`: `hub.urls`, learning resources, events, prayers, and icons.
- `hub/urls.py:60-139` combines accounts, profile mutation, fast participation, legacy user routes, calendar resources, admin helpers, and notifications in one unnamespaced URLconf.
- `bahk/views.py:10-13` serves `/docs/` as a static Coming soon page; `tests/unit/test_api_site.py:39-61` asserts no endpoint is advertised there.
- The verified anonymous routes identified in #501 are `/api/readings/`, `/api/fasts/`, `/api/feasts/`, and `/api/churches/`. They currently expose application serializers and inconsistent parameter behavior, so none can simply be re-mounted as public v1 routes.
- `docs/PRAYER_REQUESTS_API.md` documents an authenticated product feature under `/api/prayer-requests/`; it is explicitly out of the public v1 scope.

## Scope

**In scope**:

- `docs/public-api-contract.md` (new) — approved v1 base URL, ownership, support window, compatibility/deprecation policy, error-envelope target, resource inventory, and explicit exclusions.
- `bahk/public_api_urls.py` (new) — a dedicated, namespaced v1 URLconf containing only v1 routes as each becomes ready; initially no resource route may delegate to internal `hub.urls`.
- `bahk/public_api_views.py` (new) — an unauthenticated, read-only v1 root descriptor with no resource payloads or internal route inventory.
- `bahk/urls.py` — mount the dedicated URLconf at `/api/v1/` without changing `/api/` behavior.
- `tests/unit/test_api_site.py` or a dedicated URLconf test — prove `/api/v1/` is separate from internal `/api/`, and prove legacy/internal endpoints are not advertised as v1 routes.

**Out of scope**:

- Stable resource serializers (#497), request validation/error implementation (#496), endpoint-specific public routes (#494), traffic protections (#498), combined calendar data (#499), and reference documentation/publication (#500).
- Changing `/docs/`, `/api/`, client routes, authentication routes, or existing serializer response shapes.
- Any migration or database change.

## Contract contents

The new contract document must state:

1. **Base URL and versioning** — public consumers use `/api/v1/`; `/api/` is unsupported for new integrations. A future breaking contract change creates `/api/v2/`; it never mutates v1 response schemas or semantics.
2. **Initial resource inventory** — Churches, Readings, Fasts, and Feasts are planned public read-only resources. Each must be marked `planned`, `beta`, or `stable`; no route is `stable` until its serializer, validation, throttling, and contract tests land.
3. **Explicit exclusions** — accounts, profile, token, password, user-fast, fast join/leave/intention/stats/participants, notifications, admin helpers, prayers, icons, events, learning resources, uploads, and all `/hub/` routes are internal or product-client APIs.
4. **Compatibility policy** — additive optional fields are permitted in stable v1; removing/renaming fields, changing types/nullability, request meaning, auth, pagination, error codes, or HTTP status requires a new version or a documented deprecation path.
5. **Deprecation policy** — announce in documentation and release notes before removal; retain a deprecated stable public endpoint for at least 180 days unless a security, privacy, or legal emergency requires faster removal; return `Deprecation` and `Sunset` response headers during the notice period.
6. **Errors and ownership** — reserve a stable envelope with `code`, `message`, and optional `details`; #496 implements it. Name the API maintainer/release owner and require a contract-test, changelog entry, and reviewer approval for public-contract modifications.

## Steps

### Step 1: Write and approve the contract document

Create `docs/public-api-contract.md` with the contents above, including a table of public-resource status and excluded route families. Link every future issue (#494, #496–#500) to the specific contract section it fulfills. Keep this document declarative; do not document existing internal response bodies as public schemas.

**Verify**: review the document against `bahk/urls.py` and `hub/urls.py`; every currently mounted internal route family is either explicitly excluded or classified planned.

### Step 2: Reserve an isolated v1 namespace

Create `bahk/public_api_urls.py` with `app_name = "public_api"` and a URLconf intended only for supported v1 routes. Mount it at `/api/v1/` in `bahk/urls.py` using a distinct instance namespace. Do not include `hub.urls`, `prayers.urls`, or any other internal URLconf.

The initial namespace must not claim that unfinished resource endpoints are ready. Choose a single explicit root behavior consistent with the contract (for example, a small JSON service/version descriptor), or leave the prefix with no resource routes and test its 404 behavior. The behavior must not reveal internal route inventory.

**Verify**: a Django URL-resolution test proves `/api/v1/` cannot reverse/resolve a legacy endpoint such as `user/fasts/`, while existing `/api/` legacy routes keep their current reverse values.

### Step 3: Add contract-boundary tests

Add focused tests that assert:

- `/api/v1/` has its own namespace and does not include mixed internal URL patterns.
- Existing `/api/` and `/hub/` routes remain unchanged.
- `/docs/` remains Coming soon and does not advertise v1 resources before #500.
- The public-contract document is manually reviewed for all four planned resource families and explicit exclusions; do not add string-matching tests for document prose.

**Verify**: `docker exec -e IS_PRODUCTION=false <app-container> python manage.py test tests.unit.test_api_site hub.tests.test_urlconf --exclude-tag=performance --settings=tests.test_settings` exits 0.

### Step 4: Gate downstream work

Open or update the follow-on implementation tickets/PRs so they depend on this contract:

- #497 defines presentation-neutral serializers.
- #496 implements the error envelope and validation semantics.
- #498 adds throttling/cost controls before anonymous promotion.
- #494 makes individual v1 resources available once the preceding work is ready.
- #499 adds the composed calendar route.
- #500 publishes only verified docs after every readiness gate passes.

**Verify**: the contract table links each public resource and requirement to its owning follow-on issue.

## Test plan

- URL-boundary test for `/api/v1/` isolation and `/api/` compatibility.
- Existing API-site tests proving `/docs/` remains non-public.
- Verify the contract document by review; tests exercise only runtime routing and response behavior.
- No network, database migration, or production smoke test belongs in this contract-boundary plan.

## Done criteria

- [ ] `/api/v1/` is isolated from all existing internal URLconfs.
- [ ] `/api/` routes retain current behavior and are explicitly unsupported for new public integrations.
- [ ] The contract records versioning, compatibility, deprecation, resource statuses, exclusions, and ownership/release policy.
- [ ] `/docs/` remains Coming soon.
- [ ] Focused Docker tests pass.
- [ ] No serializer, validation, throttling, endpoint, schema, or migration change is included.

## STOP conditions

Stop and report rather than improvising if:

- A proposed v1 route would reuse an internal serializer with presentation-specific or user-specific fields.
- A required public resource cannot be served anonymously without side effects or external work controls.
- The implementation requires a migration, changes an existing `/api/` URL, or changes `/docs/` publication state.
- The product owner rejects `/api/v1/` as the public base URL or the 180-day support policy.

## Maintenance notes

Treat `docs/public-api-contract.md` and v1 contract tests as load-bearing API surface. A future API change must be reviewed against the compatibility policy before merging. Keep public serializer imports one-way: v1 may reuse domain services, but must not expose internal serializers or URLconfs.
