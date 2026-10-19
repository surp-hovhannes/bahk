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
