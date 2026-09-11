# Issue #498: public v1 traffic controls

Status: implemented locally on `codex/issue-498-public-traffic-controls`, based on
`codex/issue-494-public-routes` (#539). Resource activation remains gated on the
deployment checks below; the original proposed policy is recorded here for review.

## Implementation and verification record

- Added default-off registration, atomic fixed-window quotas, retry/outage responses,
  canonical bounded response caching, query bounds, credential-independent reads,
  and context-local guards at LLM, passage, and Celery dispatch boundaries.
- Added a private Prometheus exporter, seven checked alert rules, a dashboard,
  the `check_public_api_readiness` command, and `docs/public-api-operations.md`.
- Docker fallback used because Crabbox was unavailable. The standard parallel
  suite ran 1,760 tests successfully (two skipped). Subsequent focused verification
  covers the final readiness, expiry, and telemetry changes; see the handoff.
- The bounded cache check served 100 warm requests with zero SQL queries and
  local p95 around 3–6 ms; this is not a production capacity measurement.
- Prometheus validated both all seven rules and a real exporter scrape. Changed
  Python files passed Ruff, dashboard JSON parsed, and `git diff --check` passed.
- Production Redis, trusted forwarding, CDN bypass, metrics scraping, and alert
  delivery have not been configured or verified. Keep resource registration off
  until those operations checks are complete. No migrations are involved.

## Findings

- Resource routes are mounted without throttling or response caching. Issue #498's
  latest comment explicitly makes protections and monitoring a mounting gate.
- Resource views use stored data and offline feast calculation. They do not call
  the product views that fetch passage text or enqueue context generation.
- `AnalyticsTrackingMiddleware` can authenticate bearer tokens, write events and
  attribution, and update cache state before the public view runs.
  `TimezoneUpdateMiddleware` can also update a logged-in caller's profile.
- `PublicApiView.handle_exception()` reconstructs API exceptions without preserving
  the standard throttling `Retry-After` header.
- Redis is already configured, but shared with other application work. Sentry
  tracing exists; console handlers suppress INFO in production, so merely adding
  INFO logs would not establish usable traffic monitoring.
- Pagination caps page size at 100 but leaves offsets unbounded. Fast ranges have
  no span cap. Current working-tree pagination, contract, and test edits belong
  to existing work and must be preserved when implementation starts.

## Proposed policy

| Concern | Initial proposal |
| --- | --- |
| Identity | One IP-based allowance across all public-v1 routes; credentials confer no higher allowance because v1 has no authenticated tier. |
| Limits | 60 requests/minute and 1,000/hour per client, both enforced; document fixed-window boundary behavior and shared-NAT implications. |
| Methods | Charge GET, HEAD, and requests reaching v1's method/error handling. CORS preflight may be exempt only when answered without resource work. |
| Rate-limit response | HTTP 429, code `throttled`, `details.retry_after` as integer seconds, matching `Retry-After`, and `Cache-Control: no-store`. |
| Limiter outage | Fail closed with enveloped HTTP 503 (`service_unavailable`), a short retry hint, and no resource execution. |
| Response cache | Successful resource data for 300 seconds; maximum 256 KiB per entry and 10,000 live entries in a dedicated public-response namespace. |
| HTTP caches | Initially `Cache-Control: no-store` for public HTTP responses, including successful ones; application data caching provides reuse after admission checks. Verify CDN bypass before enabling routes. |
| Expensive pagination | Propose maximum offset 10,000; retain existing maximum limit 100. |
| Fast ranges | Propose maximum inclusive span of 366 days, checked after filling omitted endpoints; reject reversed effective ranges and date arithmetic overflow. |

The offset/range limits need contract entries and reviewer agreement while v1 is
pre-release. If catalogue sizes require deeper traversal, settle that before
adopting the offset limit. These rate values are a starting policy, not a capacity
claim; tune them against the bounded load check before release.

## Implementation sequence

1. **Enforce the mounting gate.** Add a default-off resource-registration setting;
   keep the existing root descriptor available. Tests explicitly enable resources.
   Do not deploy #539 independently with unprotected routes. Either land this work
   together with the predecessor stack, or put the default-off gate in #539 first.
   Disabling registration is also the operational rollback switch.

2. **Establish the public request boundary.** Bypass analytics authentication,
   event/attribution writes, and timezone profile updates for the exact public-v1
   path prefix. Preserve product route behavior. Confirm full middleware-stack
   requests with cookies and bearer tokens remain anonymous and produce no model
   writes, external passage retrieval, thumbnail generation, or task dispatch.
   Infrastructure cache writes are explicitly allowed by the updated contract.

3. **Add atomic shared admission control.** Introduce a public-only limiter,
   applied before resource queries and response-cache lookup, including the root.
   Use a small Redis script to check/update both fixed windows atomically with
   expiration and return the wait until all exhausted windows permit a request.
   Use bounded connection timeouts and a namespace separate from response data;
   response eviction must never remove limiter state. Validate the actual trusted
   proxy chain and origin access policy before trusting forwarded IP headers.
   Direct/local requests use the socket address. Test spoofed headers and proxy
   chains. Do not change global DRF throttles for shipped product clients.
   Application limits do not replace upstream volumetric protection.

4. **Normalize the error and work budgets.** Preserve retry metadata in the shared
   error boundary and expose `Retry-After` through CORS. Enforce proposed range
   and offset bounds with documented error details. Bound numeric parsing before
   conversion so oversized inputs return a controlled error. Error responses
   never enter the response cache.

5. **Cache validated resource data.** Resolve effective language, timezone, dates,
   church, pagination, and path identifiers before constructing a versioned key.
   Validate before hits as well as misses. Ignore unknown parameters in the data
   key, but preserve existing link behavior by rebuilding pagination URLs per
   request; do not cache request-specific absolute links, cookies, or headers.
   Cache entries contain only public data and count metadata. Explicit and implicit
   dates must converge; defaults must not leak across midnight or language cookies.
   Enforce entry-count and byte caps atomically, expire admission metadata, and use
   a short per-key fill lease to bound concurrent recomputation. A contender may
   briefly retry then return a controlled 503; it must not launch another fill.
   If the response cache is unavailable but admission control still works, execute
   bounded reads without caching and record the degraded mode. Use TTL-based
   freshness initially and a code-owned cache-schema version for rollback safety.

6. **Make monitoring operational.** Record route name, status, duration, throttle
   outcome, cache hit/miss/bypass, and dependency failures with low-cardinality
   fields. Exclude raw tokens, cookies, client addresses, and query strings from
   new telemetry. Use existing Sentry integrations where suitable, with an explicit
   production logger/export path; sampled traces alone are not request counters.
   Define and provision a dashboard for volume, p95 latency, 5xx, 429, cache reuse,
   cache capacity, Redis failures, and public-triggered costly work (expected zero).
   Initial alert proposals: limiter outages or any public-triggered billable work
   immediately; 5xx above 1% with at least 100 requests/5 minutes; p95 above 1 second
   for 10 minutes; 429 above 10% with at least 100 requests/5 minutes for triage.
   Confirm metric source, delivery destination, maintainer ownership, and alert
   delivery before treating monitoring as complete. Bound telemetry volume too.

7. **Document and release.** Update the public contract/changelog and add an
   operations runbook covering fair use, retry/backoff, cache staleness, proxy
   identity, outage behavior, dashboard links, and disabling resources. Keep
   `/docs/` Coming soon for #500. Enable resource registration only after policy
   review, verification, proxy/CDN checks, and monitoring delivery are complete.

## Verification

- Unit/contract coverage: exact limit/reset boundaries, aggregate route allowance,
  header spoofing, credential equivalence, 429/503 envelope and CORS headers,
  cached-hit throttling, invalid inputs, effective range/offset limits, language
  and midnight isolation, pagination links, freshness, and gate on/off behavior.
- Full middleware tests prove no public-request model writes or billable calls,
  including session and bearer credentials; product behavior stays covered.
- Real Redis integration tests prove concurrent admission counts, expiry, cache
  caps, fill contention, and outage behavior. Use isolated test namespaces and
  cleanup only those keys; never clear shared Redis. DummyCache is suitable for
  unrelated parallel route tests, but cannot verify limiter/cache correctness.
- A bounded runtime load check exercises repeated and diverse queries, deep
  offsets, and date ranges; check latency, query work, memory bounds, and exported
  telemetry. This work explicitly includes performance behavior.
- Use Crabbox when installed, per repo instructions; otherwise discover the local
  app container and run Django checks there. Run focused new tests, the standard
  suite excluding unrelated performance/slow tests, changed-file lint, and diff
  checks. Add only relevant performance tests to the focused run.

## Decisions to settle during implementation

- Confirm proposed rate, cache, offset, and date-range values with the maintainer.
- Verify deployment proxy/header trust and CDN bypass rather than assume a provider.
- Confirm Redis capacity/isolation, including limiter-state retention under memory
  pressure; a separate key prefix alone is not memory or eviction isolation.
- Confirm monitoring access, metric backend, alert destination, and owner. Code
  instrumentation and a runbook alone do not satisfy the issue's monitoring gate.

Reference: DRF documents that its built-in throttles use non-atomic operations and
are not denial-of-service protection:
https://www.django-rest-framework.org/api-guide/throttling/
