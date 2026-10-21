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

## Notification lifecycle (v2 vocabulary)

This vocabulary supports surp-hovhannes/bahk_react_native#460. PR1 registers
codes and documents contracts; PR2 adds flag-gated server send telemetry.
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
| notification_send | notif_id, kind, destination, platform, outcome, error_code, ticket_id, correlatable | Attempt sent to push provider and its outcome |
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
The send producer (PR2) writes `notification_send` rows only while `NOTIFICATION_TELEMETRY_ENABLED` is on.

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


### Server send telemetry (PR2)

Deploy with `NOTIFICATION_TELEMETRY_ENABLED=False` (the default). Run
`python manage.py init_event_types`, then `python manage.py init_event_types --check`
first; resolve missing/inactive types before enabling
`NOTIFICATION_TELEMETRY_ENABLED=True`. Disable the setting to stop both writes and
correlation augmentation. No migration, receipt polling, deletion, retries, token
policy changes, client endpoint, or RN/OTA publication is included.

Each attempted per-token publish records `notification_send` with constant title
`Notification send`, empty description, recipient user (nullable), and
`source=server_domain`. The sender resolves the active EventType once per call,
collects rows in token-attempt order, and bulk inserts after the loop (batches of
500) inside an atomic savepoint. Because bulk inserts bypass save/full_clean/signals,
the writer explicitly supplies the constant title, empty description, user_id,
null IP, empty user agent and schema version 2; timestamp uses its model default.
No User rows are loaded by the token query. No request is passed. Missing/inactive
types, lookup failures and write errors produce constant warnings and cannot alter
send counts or returns. Events contain
no token, DeviceToken primary key, message, original data, content, resource IDs,
IP address, or user agent. Platform is `ios`, `android`, `web`, or `unknown`.

`kind` is independent of notification preference filtering. Unknown kinds become
`unclassified`. Scheduled fast reminders map to `upcoming_fast`, `ongoing_fast`,
`daily_fast`, `culmination_feast`; weekly prayer summaries to
`weekly_prayer_requests`; completion to `prayer_request_completed`; thanks to
`prayer_thanks`; participant milestones to `fast_milestone`; nonjoin, inactive,
activity-feed, and prayer-acceptance nudges to their corresponding `nudge_*`
kinds; manual admin sends to `admin_manual`; admin/API test sends to `test`.
There is currently no push caller for `post_fast` (encouragement is email only).

Destination uses the shared `ROUTE_FAMILIES` contract above: split at the first
slash and fullmatch the tail. A top-level `url` maps to `url`; legacy ID keys
identify only their family. Empty data yields `none`, unknown routes `other`.
No IDs are copied.

Each token gets a fresh `secrets.token_urlsafe(12)` ID and its own shallow copy
of outgoing data with flat `fp_nid` / `fp_kind` keys. A conservative JSON envelope
size check of body/data reserves 512 bytes of headroom for provider-side keys
(4096 - 512 bytes): such sends retain their
original payload and still record an attempt, but cannot correlate a client open.
Existing oversize payloads are left to the provider as before. This is a coverage
limitation; the flag does not guarantee every event has client correlation.
`correlatable` is true only when augmentation succeeds; oversized envelopes or
preparation failures after ID allocation still record outcomes with false.
PR7 reports must exclude false rows from open-rate denominators.

A ticket with status `ok` means `provider_accepted`; status `error` means
`provider_rejected`. Error codes are limited to `DeviceNotRegistered`,
`MessageTooBig`, `MessageRateExceeded`, `MismatchSenderId`, `InvalidCredentials`,
`DeveloperError`, else `other`. Ticket IDs are optional, at most 128 ASCII
alphanumeric/underscore/hyphen characters; unexpected IDs are omitted. Exceptions
with a ticket attached are classified from that ticket; other exceptions and
unknown/missing ticket shapes use `send_error` (outcome could not be established).
Raw exception text never enters an event or sender log.

Legacy `sent` remains the count of non-raising publishes to the push provider,
not delivery. It may include a returned error ticket: telemetry distinguishes
those rejections without changing existing result dictionaries. Only the existing
`DeviceNotRegisteredError` exception branch deactivates a token; returned error
tickets do not add deactivation. No receipt-based disabling is added.

SDK verification: local `python3` has no installed `exponent_server_sdk`, and
requirements are unpinned; deployed API compatibility remains unverified. The
[upstream SDK source](https://github.com/expo-community/expo-server-sdk-python/blob/master/exponent_server_sdk/__init__.py)
was inspected on 2026-10-20: `publish()` returns a `PushTicket` with `status`,
`details`, and `id`; `validate_response()` separately raises ticket errors carrying
`push_response`. The producer reads these attributes (also accepts dict-shaped
tickets), without calling validation. Tests construct real SDK tickets when CI
installs its requirements. These upstream observations do not identify the
deployed version. Receipt APIs remain outside PR2.

During rolling deploys, producers omit the new task `kind` keyword while the
flag is off: older Celery workers reject unknown keywords and would lose pushes.
All task producers with explicit kinds use `telemetry_task_kwargs`; the five
scheduled senders derive the default kind from their existing notification_type.
Keep the flag off throughout worker replacement. This PR does not enable it.

Dead-device tickets are currently counted as sent and tokens are not
auto-deactivated (pre-existing; separate decision). The legacy
DeviceNotRegisteredError exception branch remains unchanged in behavior;
publish() in SDK 2.2.0 returns error tickets without raising that exception.
Result invalid_tokens contains hashed identifiers, and errors contains exception
class names, never raw tokens or exception text. Callers only count invalid tokens
or join error labels. Pinning the SDK still needs a confirmed deployed version;
no dependency pin is added here.
