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
include skipped counts and circuit state. Admin messages use plain language.
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

## Offline admin warning screenshot

The existing Visual QA workflow can use `qa/visual/expo-transport-warning.json`
as its plan input after the commit is available to CI. This worker does not start
CI or publish the branch. Only plans containing `admin-retry-circuit-warning`
seed the synthetic iOS device belonging to visual-qa-anna, then the plan filters the device changelist, selects it, and
runs the actual **Send test notification to selected devices** action. The normal
`report_transport_status` renders **The notification service repeatedly rejected send requests.**
alongside **The notification request could not be completed. Send requests completed: 0.
Send requests with errors: 1. Delivery to devices is not confirmed.** and
**Contact support before sending again; some devices may receive duplicates.** Inspect both mobile and desktop screenshots and manifest in the
`visual-qa-<run_label>` artifact; a missing warning fails the scenario wait.

Only `tests.visual_qa_settings` installs the fixture middleware. For that
authenticated superuser POST only, it substitutes a provider that always raises structured 503 rejection, and removes sleeps. The real classifier exhausts
three attempts and opens its circuit; nothing is accepted, no receipt is created,
and fixture telemetry is disabled. The seed and middleware fail closed under any
other settings module. Production settings, URLs and provider implementation are
unchanged; no endpoint or environment toggle is added. All other requests block
provider construction: the synchronous admin sender does not consult
`SEND_PUSH_NOTIFICATIONS`, so that setting alone is insufficient. The CI database
contains synthetic data only.

The middleware serializes requests in the disposable QA server because its mock
and settings contexts are process-global; contexts restore even on exceptions.
Do not install this middleware in a production server or reuse these fixtures
with production data. The screenshot demonstrates the circuit warning with zero
skips, not a real outage, receipt delivery, or consent concurrency. Automated
regressions verify the actual action/HTML and isolation; CI screenshot capture
and independent review remain separate checks. Roll back this fixture by reverting
its commit; it has no schema or production configuration effects.

## Admin wording and count limits

Both synchronous admin actions share the same summary for all outcomes:
“Send requests completed: N. Send requests with errors: N. Delivery to devices is
not confirmed.” Completed requests retain the legacy `sent` count, which can include
returned rejection tickets; this is neither confirmed acceptance nor delivery.
An unsuccessful result prefixes “The notification request could not be completed.”
Errors (including partial failures and unknown outcomes) advise contacting support
before sending again because some devices may receive duplicates. No automatic or
manual replay is recommended. No raw `errors` or exception text is displayed.
Diagnostic details remain in existing server logs; the sender result contract and
sending behavior are unchanged.

A nonzero skipped count adds “Not sent to N devices because they were no longer
eligible for this notification. No action is needed for these devices.” It does
not infer a particular preference change. Invalid registrations add “Notifications
were turned off for N devices because the notification service no longer recognizes
their registrations.” Identifiers are never displayed in these notices.

Background sending says “Notifications queued for background sending to N devices
(N users). Delivery is not confirmed. Do not send again before checking with
support; some devices may receive duplicates.” An unexpected manual-view exception
says “The notification request could not be completed. Delivery is not confirmed.
Contact support before sending again; some devices may receive duplicates.” This
also covers an uncertain queue submission, without promising delivery or assuming
nothing was sent. Routine form validation remains separate.

### Plain-language change self-review

Reviewed both synchronous actions, queued sends, partial/all failures, no available
devices, eligibility skips, invalid registrations, and unexpected manual-view
exceptions. Reviewed the full synthetic warning plus failure output (not just the
warning selector). The offline plan now requires both messages and the support
advice. No screenshot was supplied with this task; fresh mobile/desktop capture
and visual inspection remain the PM's follow-up. The scenario identifier is kept
for compatibility with existing fixture seeding.

The diff changes presentation, regression assertions, the QA plan, and this
reference only. No sender, result fields, audience selection, task arguments,
retry rules, migrations, or schema were changed. Existing diagnostic logging is
retained. Python 3.11 syntax parsing, visual plan validation, and `git diff --check`
passed locally. `ruff check .` could not run because Ruff is absent. Focused
Django regressions (`notifications.tests.test_transport` and
`tests.test_visual_qa_transport`) remain unexecuted: local Docker is unavailable,
and the warmed Crabbox has no Docker. Automatic approval review blocked a further
Crabbox probe because its sync exports the private checkout to an external host.
No dependency installations were attempted. Full CI and fresh visual QA are
reserved for PM review; do not treat static checks as a test or lint pass.
