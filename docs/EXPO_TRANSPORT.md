# Expo publish transport handling

Only structured whole-request rejections without ticket data permit retry on
HTTP 429/5xx. Bare HTTP failures, timeouts, malformed/mixed responses and accepted
tickets are never replayed. Each device has at most three attempts (1s/2s waits).
A per-call circuit opens after three total transient rejections, even across
successful devices: at most two waits and three seconds of backoff per call.
The exhausted rejection counts as failed; every later eligible device gets one
request without retry or backoff. Sustained outage load is one request per device
plus at most two retries per call. Later transient rejects have no recovery, while
healthy recipients still get their attempt. Consent skips produce no send telemetry
or receipt entries. A single actionable error logs the circuit opening; task logs
include skipped counts and circuit state, also visible in synchronous admin results.
The circuit does not persist across calls or admin chunks.

The normal path uses the materialized audience without extra eligibility reads.
After any backoff, each subsequent publish checks current token identity, owner,
activity and notification preference using a narrow query. Revocation during
backoff is a skip, without failure telemetry. There remains a race between the
check and provider acceptance, and ordinary no-backoff sends use snapshot consent.

DB checks and bookkeeping use savepoints so recoverable database errors do not
poison a caller transaction or abandon prior accepted receipt mappings. Accepted
publishes remain counted as sent even if last-used bookkeeping fails. Receipt
storage and telemetry remain best-effort; a lost database connection or an outer
transaction rollback can still prevent telemetry persistence. No send is retried
for a local database error. Delivery is established separately by receipt polling.
Transport details and generic error names are capped at 20 entries each; omitted
transport details have an explicit count. Status and logs exclude provider text
and raw tokens. Existing invalid-token reporting retains its hashed identifiers.

The pinned SDK is `exponent_server_sdk==2.2.0`. Its parser raises Requests
`HTTPError` for non-JSON HTTP failures: these are ambiguous and never safely
retried. This improves classification and sanitized status evidence, without
claiming recovery of the observed YZ events. [Issue #605](https://github.com/surp-hovhannes/bahk/issues/605)
identifies YV as 503 but its response shape is unknown; YZ's exact status remains
unverified. Tests exercise the real SDK parser through mocked HTTP sessions.

Ambiguous sends may be lost to avoid duplicates. Task redelivery has no shared
idempotency guarantee. The circuit disables recovery for later transient rejects
after unrelated structured failures; provider request time still scales with
eligible devices. Requests timeouts
are not total wall-clock deadlines. Rollback by reverting the repair commit;
there are no schema, migration or configuration changes to undo.
