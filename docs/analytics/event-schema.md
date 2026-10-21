# Event schema v2

`events/event_schema.py` defines the contract and read-time classifications.
Absent `data.schema_version` means v1 (legacy); new `Event.create_event` calls
stamp v2 by default, preserving an explicitly supplied version. Direct ORM
writers bypass this convenience method and must be interpreted accordingly.
This is a JSON data contract, with no database schema change.

## Meaning and classification

Meaningful engagement codes are exactly `checklist_used`, `prayer_set_viewed`,
`devotional_viewed`, `user_joined_fast`, and `prayer_request_accepted`.
Viewing is not practice completion; account creation is not activation.
A meaningful return signal is evidence of opening content, not finishing it.

- Action: checklist use, fast join/leave, login/logout/account creation, content
  publication and fast administration, prayer request creation/acceptance/completion/thanks.
  Only actions in the meaningful allowlist count as meaningful engagement.
- Return-signal: `prayer_set_viewed`, `devotional_viewed`.
- Exposure-noise: `screen_view`, `app_open`, `session_start`, `session_end`,
  `prayer_viewed`, `prayer_request_viewed`, `tutorial_video_viewed`, fast
  beginning/ending, devotional availability and milestone notifications.

Unknown/custom codes have no classification and are not meaningful by default.
Screen-view metrics use `events/event_semantics.py` to count only `client_ui`
rows with integer schema version >= 2. Other classifications do not by themselves
change report inclusion. Shared event aggregates also exclude legacy screen views.

## Sources and payload keys

Source vocabulary: `client_ui` (explicit UI emission), `server_inferred`
(request/session inference), `server_domain` (domain action). Legacy `api` and
`app_ui` source values are historical only. Existing non-screen producers are
not retrofitted with a source: missing source remains unknown, not client UI.
All convenience-method payloads add `schema_version`; existing producers may
also add `lang` when a request supplies it. Activity feed items copy event data
without `schema_version`, so it never reaches the user-facing feed API.

| Type | Data keys (producer-dependent, nullable where unavailable) |
| --- | --- |
| screen_view v2 | schema_version, source=`client_ui`, screen, optional previous_screen, session_id, app_version, platform |
| app_open / session_start | session_id, path, app_version, platform |
| session_end | session_id, duration_seconds, requests |
| checklist_used | fast_id, fast_name, action, context |
| devotional_viewed | devotional_id, fast_id, day |
| prayer_set_viewed | prayer_set_id, church_id, church_name |
| prayer_viewed | prayer_id, church_id, fast_id, category, title |
| prayer_request_viewed | prayer_request_id, status, title |
| tutorial_video_viewed | video_id, title |

Domain event payloads remain producer-specific (for example fast/participation
identifiers for joins, prayer request identifiers for acceptance); v2 does not
introduce new required domain keys or reinterpret them as completion evidence.

## True UI screen views and privacy

Authenticated clients POST `/api/events/track/screen-view/` when a screen becomes
visible, with `screen` and optionally `previous_screen`. Both must be strings
matching `^[a-z0-9_]{1,64}$`, excluding digits-only identifiers; an omitted, null
or empty `previous_screen` means none. Use stable screen names, never paths,
object IDs or free text. Invalid/missing names return 400; valid requests return
200 (`status: skipped` if the `screen_view` type is missing or inactive). The
scoped per-user throttle defaults to 120/minute (`SCREEN_VIEW_THROTTLE_RATE`).

The endpoint copies only validated names and validated app/platform headers:
`X-App-Version` must match `^[0-9A-Za-z.+-]{1,32}$` and `X-Platform` `^[a-z]{1,16}$`,
otherwise the field is stored as null. Session ID comes from the middleware
session cache (`events.middleware.session_cache_key`), or null if unavailable.
It passes no request to `create_event`: IP stays null, user agent stays empty,
and no request language, path, query string or arbitrary body fields are copied
into this event. Existing session/app-open metadata
behavior is unchanged; this privacy guarantee applies to the new screen event.

Authenticated GETs no longer create screen views. `X-Screen`, `?screen=` and
`X-API-Request` do not cause screen tracking. At the deployment cutoff,
**screen_view volume drops and must not be trended across the cutoff**.
Client adoption also affects coverage: compare v2 client UI cohorts separately.

## Historical data policy

Backfill means read-time classification only: NO writes, NO deletes, NO
migrations. Legacy rows (including IP/UA and API-inferred views) remain untouched.
Do not treat an absent schema version or legacy source as a true UI screen view.

## Deploy runbook

No manual cache invalidation is needed. This change bumps
`AnalyticsCacheService.CACHE_VERSION`, so every worker ignores aggregates cached
before the deploy. Do **not** run `AnalyticsCacheService.invalidate_all_analytics()`:
its `analytics:*` pattern also deletes the middleware's `analytics:session:*` and
`analytics:last_seen:*` keys, which restarts every active user's session (spurious
`app_open`/`session_start`, no `session_end`). `python manage.py warm_analytics_cache`
is safe to run afterwards if you want warm caches.

Record the deploy timestamp. Query `Event` for `screen_view` rows at or after that
timestamp with `data__source__in=['api', 'app_ui']`; expect zero. For a controlled
successful POST to `/api/events/track/screen-view/`, compare before/after rows for
the test user and timestamp window: expect exactly one new `client_ui` row with
integer schema version >= 2 and the submitted stable screen name. GET requests
must add none. Reports and dashboards exclude historical rows at read time; the
engagement report exposes only an aggregate legacy exclusion count. **Do not
trend screen_view metrics across the client_ui cutoff**; client rollout changes
coverage as well as event meaning.

## Notification lifecycle (v2 vocabulary; PR1)

This vocabulary supports surp-hovhannes/bahk_react_native#460. PR1 registers
codes and documents contracts only; it adds no send or endpoint behavior.
All five codes below are exposure-noise, never actions or meaningful engagement.
They have category `notification`, require no target, and new registrations have
`track_in_analytics=False`. Existing registrations are never overwritten.

Use “sent to push provider”, “accepted by provider”, “provider receipt” and
“opened”. Provider acceptance is never device delivery; delivery is not measured.
A provider receipt is provider evidence only. Do not add token auto-disabling.

The following per-type keys supplement the common `schema_version=2` envelope;
`events/event_schema.py` exposes them as `NOTIFICATION_PAYLOAD_KEYS`. These are
contracts for later producers, not runtime validation in PR1.

| Type | Payload keys | Meaning |
| --- | --- | --- |
| notification_prompt | stage, choice, trigger | Permission prompt lifecycle |
| notification_send | notif_id, kind, destination, platform, outcome, error_code, ticket_id | Attempt sent to push provider and its outcome |
| notification_receipt | notif_id, ticket_id, receipt_status, error_code, lag_seconds | Provider receipt and elapsed lag |
| notification_opened | notif_id, entry, app_state, matched, kind | Notification opened |
| notification_destination | notif_id, destination, result, matched | Destination resolution |

Only bounded vocabulary, opaque correlation/provider identifiers, booleans and
numeric lag belong here. Later producers must validate these; unknown or
unavailable values must not become raw exception text. No free text, push token,
message/body, arbitrary message/data blobs, prayer content, IP address or user
agent may be recorded. Do not pass a request to `Event.create_event` for this
telemetry. Remaining field vocabularies/nullability must be verified in PR2/PR3
before emission; these key contracts alone do not authorize arbitrary values.

`NOTIFICATION_KINDS` is the allowlist: `upcoming_fast`, `ongoing_fast`,
`daily_fast`, `culmination_feast`, `weekly_prayer_requests`,
`prayer_request_completed`, `prayer_thanks`, `fast_milestone`,
`nudge_nonjoin`, `nudge_inactive`, `nudge_activity_feed`,
`nudge_prayer_acceptance`, `admin_manual`, `test`, `unclassified`.

`NOTIFICATION_DESTINATIONS` is the allowlist: `fast`, `devotional`, `prayer_set`,
`prayer_request`, `prayer_requests`, `learn`, `activity`, `prayer`, `url`, `none`, `other`.
`ROUTE_FAMILIES` maps screen families to full tail regexes: numeric IDs for
fast/devotional/prayer/prayer-request/prayer-set, typed IDs for learn, and empty
tails for activity/prayer-requests. Hyphens normalize to underscores in destination
labels; a top-level `url` key maps directly to `url`. Unknown routes use `other`.
`NOTIFICATION_OUTCOMES` allows `provider_accepted`, `provider_rejected`,
`send_error`. `NOTIFICATION_REPORTING_GROUPS` maps each kind to itself except
`prayer_thanks` and `prayer_request_completed`, which both map to
`prayer_request_notifications` (D7). Any notification report must apply k=10
small-cell suppression, including derived cells that could reveal a small count.

### Correlation and attribution

Generate a random `secrets.token_urlsafe(12)` correlation ID for each message ×
device token; store it as `notif_id`. Carry it and the allowlisted kind in push
data as flat `fp_nid` and `fp_kind` keys. These are never navigation keys and must
not change navigation behavior. Never derive the ID from a token or user identity.

Attribute meaningful activity only within 30 minutes after an open, in the same
app session where available. This is association, not causation. Always report
coverage (including missing correlation/session information); do not trend across
rollout because adoption changes coverage. No attribution computation is added
in PR1. Intended lifecycle retention is **400 days, NOT enforced**: no deletion
mechanism exists for these records. This is a policy intention, not a guarantee.

### Registration and dashboard isolation

At deploy run, in order:

```bash
python manage.py init_event_types
python manage.py init_event_types --check
```

`--check` is read-only and checks every core code. Missing codes and incorrect
lifecycle attributes (category must be `notification`, requires_target and
track_in_analytics must both be false) exit 1, with each mismatch reported.
Inactive codes are informational and exit 0 if there are no other problems:
deactivation is the operator's telemetry off switch. Default initialization only
creates missing rows. Run `python manage.py init_event_types --force` to repair
the three lifecycle attributes on the five lifecycle rows only; it never changes
is_active or existing non-lifecycle rows. `--check --force` is rejected.
A missed registration step disables this telemetry; future producers must skip
missing/inactive types without interrupting sending or navigation.

No database migration or migration tooling is needed: `EventType.code` is a
plain CharField without choices, `notification` already exists in category
choices, and no model field definitions change. Registration creates data through
the existing command, not a schema migration.

Audit: `analytics_events()` excludes lifecycle type IDs using an uncorrelated
subquery on the small event-type table, independent of category or the
informational track_in_analytics flag, without joining that table. Existing
participation-noop and legacy-screen filtering remains in place. Lifecycle saves
skip analytics invalidation and activity-feed dispatch, including async dispatch.
User-visible event querysets and feed backfills share the same exclusion, before
per-event feed existence checks. The feed model's fixed code map also rejects them.
Lifecycle titles are constant in code, even when callers supply free text;
request IP and user-agent metadata are never stored for these events.

### Expo SDK verification for PR2/PR3

`requirements.txt` lists `exponent_server_sdk` without a pin. There is no Python
lock file in the repository. `.devcontainer/Dockerfile` and
`.github/workflows/django_ci.yml` install requirements without resolving a
recorded version; `.cursor/Dockerfile` does not identify an SDK version either.
No checked-in CI installation log establishes the deployed version. The actual
deployed version is therefore **unknown**; PR1 leaves the dependency unchanged
and installs nothing.

The current sender calls `PushClient.publish(PushMessage(...))` and discards its
return value. PR2/PR3 must inspect the actual installed SDK and verify the
`PushTicket` status/id surface (including missing IDs, provider rejections and
exception behavior), plus `PushClient.check_receipts_multiple` or the installed
version's equivalent: input ticket IDs, returned receipt status/error details,
batch limits, missing/pending receipts and retry behavior. These are verification
requirements, not confirmed APIs for the unknown deployed version. Normalize
error codes without raw messages; distinguish ticket acceptance from a provider
receipt and both from an open. No sender behavior changes in PR1.


### Client notification ingestion API (PR4)

`POST /api/events/track/notification/` requires authentication and has its own
per-user `notification_telemetry` throttle (default 30/min, configured by
`NOTIFICATION_TELEMETRY_THROTTLE_RATE`). The JSON body must be an object:

- `event: prompt`: `stage` is `shown`, `selected`, or `os_result`; `trigger` is
  `first_prayer`, `first_fast_join`, `settings`, or `other`. `shown` has no choice
  (absent/null accepted and omitted); `selected` requires `enable` or `not_now`;
  `os_result` requires `granted`, `denied`, or `undetermined`.
- `event: opened`: `notif_id` must fully match `[A-Za-z0-9_-]{16}`;
  `entry` is `tap` or `cold_start`; `app_state` is `cold` or `warm`.
- `event: destination`: the same `notif_id` validation; `destination` comes
  from `NOTIFICATION_DESTINATIONS`; `result` is `opened`, `unresolved`, or `rejected`.

Unknown fields are ignored. Invalid body fields return 400. Each accepted row
has `source=client_ui`, the cached analytics `session_id` when available, and
validated headers: `X-App-Version` matching `[0-9A-Za-z.+-]{1,32}` and
`X-Platform` matching `[a-z]{1,16}` (the same envelope as screen views). Missing/invalid headers become null.
No request, IP, user agent, client kind, token, message, navigation key or body
blob is copied into these events. Titles are constants.

Open/destination matching searches only the authenticated user's
`notification_send` rows within the last 14 days with the same `notif_id`.
`matched` records whether a row exists; open `kind` is copied from the newest
matching send and checked against `NOTIFICATION_KINDS`, otherwise `unclassified`.
Unmatched events are retained. Matching indicates association, not causation.
`matched` and `kind` are ingest-time hints, independent of response status.
PR7 reports must join opened/destination rows to `notification_send` on `notif_id`
at report time, and expose a same-user flag; out-of-order ingestion can leave
hints incomplete. Ingest matching remains restricted to the authenticated user.

An optional `occurred_at` must be an ISO-8601 timestamp with seconds and timezone
(`Z` or numeric offset), at most 24 hours old or 5 minutes ahead. Invalid values
return 400; valid values are normalized to UTC ISO in data. Reports use
`occurred_at` when present, otherwise the server timestamp. Session ID is filled
only when time is absent or no more than 30 minutes old. `cold_start` with `warm`,
and destination `none` with result `opened`, return 400. Only JSON is supported;
form and multipart bodies return 415 when ingestion is enabled.

Opened/destination events deduplicate per user, event type and `notif_id` against
rows ingested within 14 days (using the existing user/timestamp index to bound
the search). Duplicates return 200 `{"status":"duplicate"}` without a write.
This is best-effort under simultaneous requests, not a database uniqueness
constraint. Reports still take the first open per `notif_id`. Prompt attempts
are capped at 10 per user/stage/trigger per UTC ingestion day using a cache
counter with a 2-day timeout; over-cap returns 200 `{"status":"capped"}`.
The cap fails open on cache errors and counts attempts even if a later write
fails. It is a volume control, not a billing or security boundary.

Missing/inactive types skip before matching or view cache reads. Matching and
creation are atomic: a post-insert failure rolls the lifecycle row back.
Missing/inactive types and failures inside the view return
200 `{"status":"skipped"}`; unexpected failures log a constant message with
exception information, without logging the request payload. Successful writes
return 200 `{"status":"ok"}`. Throttle and AnalyticsTrackingMiddleware cache
failures happen before the view and can surface as 5xx; clients should drop
5xx responses and must not retry hot. Authentication and throttling still apply
when disabled.

Deploy runbook addition: leave shared `NOTIFICATION_TELEMETRY_ENABLED=False`
(the decouple default) while deploying the server and registering/checking types
with the commands above. Enabling collection is outside this PR;
when off, the endpoint returns `skipped` and writes no lifecycle row. This flag
name is shared with the sender telemetry lane. No migration, deletion, retention
enforcement or token-disabling change is introduced. OTA/RN publishing remains held.
