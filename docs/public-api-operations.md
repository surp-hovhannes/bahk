# Public API traffic controls (#498)

Resource registration is off by default. This code can land with the #539 stack
while the resources remain unmounted. `/docs/` stays Coming soon for #500.
Deploying the code does not certify proxy trust, CDN policy, or alert delivery.

## Configuration and activation

1. Provision a dedicated Redis instance with an explicit memory ceiling and
   `maxmemory-policy noeviction`. Do not use the application cache or Celery broker
   instance, even with another logical database: Redis eviction is instance-wide.
   Set `PUBLIC_API_REDIS_URL` via the deployment secret store. This is needed for
   the root descriptor too; without it, public requests return 503 and retry later.
2. Capacity-plan the store. The configured response ceiling is 10,000 entries of
   up to 256 KiB (about 2.44 GiB of payload at the theoretical maximum), plus index,
   lease, limiter, and telemetry overhead. Most catalogue responses should be far
   smaller. Reserve limiter headroom based on measured traffic. Under memory
   pressure no keys are evicted: writes fail and public admission fails closed.
   Do not raise the response ceiling without reviewing that operational risk.
3. Set `PUBLIC_API_METRICS_TOKEN` to at least 32 cryptographically random characters.
   This separate bearer token authorizes `GET /internal/public-api-metrics/` only.
   Restrict that endpoint to the monitoring network as an additional boundary.
   Never put the token in a URL or commit it to a scrape configuration.
4. Verify the actual proxy chain and origin reachability. Set
   `PUBLIC_API_TRUSTED_PROXIES` to a comma-separated allowlist of proxy CIDRs.
   Never use `0.0.0.0/0` or `::/0`. The limiter starts with `REMOTE_ADDR`, walks
   `X-Forwarded-For` right-to-left only through trusted hops, and ignores supplied
   forwarding headers from untrusted peers. A trusted peer with a missing or
   malformed chain gets 400. Confirm two real clients get separate allowances,
   and that a caller cannot obtain a new allowance by forging the header.
   With no trusted proxies, only `REMOTE_ADDR` is used; behind a proxy this may
   accidentally place every caller in one bucket, so verify before activation.
5. Configure the edge/CDN to bypass public response caching and provide upstream
   volumetric protection. Application quotas are not a DDoS boundary. A CDN that
   ignores `Cache-Control: no-store` could bypass the limiter. Verify actual headers
   and cache status, including HEAD, errors, and preflight.
6. Configure metrics scraping, import the supplied dashboard and alert rules, assign
   the API maintainer/on-call destination, and verify delivery with a test alert.
7. In the project runtime run `python manage.py check_public_api_readiness`.
   The command checks configuration and Redis policy read-only; managed Redis may
   require an operator to grant inspection or independently verify a rejected CONFIG
   command. Do not treat a failed inspection as proof of readiness.
8. Complete the smoke checks below, then set `PUBLIC_API_RESOURCES_ENABLED=true`
   and restart/redeploy application workers. Startup rejects missing Redis/token
   configuration. URL registration is evaluated at process startup.

Defaults are 60 requests/minute and 1,000/hour. Optional rate environment variables
are `PUBLIC_API_RATE_MINUTE` and `PUBLIC_API_RATE_HOUR`; changing them requires a
policy/documentation update. `PUBLIC_API_REDIS_PREFIX` isolates deployments. A
prefix is not a substitute for an independent Redis instance.

## Monitoring

The private endpoint exports aggregate Prometheus text metrics from Redis:

| Metric | Purpose |
| --- | --- |
| `public_api_requests_total{route,status}` | Request volume, errors, and throttling. Early rejections use route `unresolved`. |
| `public_api_duration_seconds_bucket`, `_sum`, `_count` | End-to-end application latency histogram, including admission/cache work. |
| `public_api_cache_total{outcome}` | Hits, misses, bypasses, capacity, and fill contention. |
| `public_api_cache_entries` | Live payload/fill reservations against the 10,000-entry cap. |
| `public_api_store_up` | Read/write store health, including noeviction write rejection. |
| `public_api_blocked_work_total{kind}` | Rejected LLM, passage, and task calls from public reads; alert on any increase. |

Scrape once per 30 seconds using a Prometheus authorization credentials file.
The endpoint aggregates all app workers sharing the store: scrape one service
target, not every replica, to avoid double counting. Also monitor scrape `up`.
Metrics expire after 24 hours without public traffic and reset on store loss;
use counter-aware `rate`/`increase` queries. New fields contain no raw IPs, query
strings, cookies, or tokens. IP keys use an HMAC and expire after their window.
Existing Sentry configuration remains responsible for its own request-data policy.

Import [the dashboard](../ops/public-api-dashboard.json) and
[alert rules](../ops/public-api-alerts.yml). Set the scrape job to `bahk-public-api`.
The dashboard uses a Prometheus datasource variable; choose the deployed source.
Alert delivery/receivers are deployment configuration and are not provisioned by
this repository. Keep the gate off until an operator confirms them.

Redis telemetry failures and blocked work also emit ERROR events through the
`bahk.public_api` logger/Sentry integration, limited to one per kind per minute
per worker. When the store is down, request counters cannot increment; the scrape
health signal and logs cover that gap. Monitor Redis memory and service-wide
Celery/provider spend through existing infrastructure monitoring as well. The
blocked-work metric measures prevented attempts, not provider billing or jobs
initiated by unrelated workers. The guards cover the shared LLM request wrappers,
Bible passage client, and this application's normal/eager Celery dispatch paths;
new provider integrations must preserve this boundary and add tests.

## Smoke checks and testing

- With registration off, resource routes return 404 when admission is available;
  the root returns the pre-release descriptor. No resource URL is registered.
- In a staging deployment with registration on, check anonymous GET and HEAD,
  required parameters, limit/offset errors, equivalent requests, EN/HY responses,
  and CORS `Retry-After`. Verify HTTP cache bypass through the actual edge.
- Send requests up to the approved limit from one controlled client; verify 429
  and retry timing without affecting unrelated clients. Do not run a production
  load test as a deployment smoke check.
- In staging, interrupt the public store and verify 503 plus monitoring delivery.
  Restore it and confirm recovery. Simulate a blocked work attempt with the unit
  tests; do not trigger real billable operations to test the guard.
- In the project runtime, run focused integration tests with an isolated Redis:
  `PUBLIC_API_TEST_REDIS_URL=redis://redis:6379/15 python manage.py test tests.unit.test_public_api_traffic --noinput --settings=tests.test_settings`.
  Test keys use random prefixes and cleanup only those keys. Without that variable,
  Redis integration tests explicitly skip. Regular contract tests keep traffic
  and caching disabled to avoid cross-worker contamination.
- Run the normal Django suite and the focused performance checks before release.
  Local timing is a regression signal, not evidence of production capacity.

## Cache behavior and rollback

Only serialized public data and collection counts are cached. The cache does not
store headers, credential state, or pagination URLs. Language follows Django's
effective locale; omitted date ranges resolve once per request. Equivalent inputs
share a key, and midnight produces a different key. Validation precedes cache hits.
Five-minute TTLs bound ordinary data staleness; there are no model invalidation
signals in this change. Cache schema `v1` is code-owned: bump it in the same change
as a cached payload change so an older deployment cannot read incompatible data.

Fill leases last 30 seconds. Other callers get a one-second 503 retry hint instead
of duplicating the fill. A full index or oversized result bypasses caching under
the normal quota and query bounds. Payloads and leases expire, and expired index
members are pruned atomically on admission. Successful fills refresh the index's
TTL; a crashed fill cannot retain capacity indefinitely.

To stop resource access, set `PUBLIC_API_RESOURCES_ENABLED=false` and restart all
workers. Do not roll back to #539 alone, which mounts routes without protections;
use a revision retaining the default-off gate. No schema migration or data rollback
is required. Never flush a shared Redis store as a recovery step.
