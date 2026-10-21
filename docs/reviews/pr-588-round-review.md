# PR #588 review round

All accepted items are implemented. No model fields, migrations, sender/token
behavior, dashboards, retention enforcement, or telemetry flag enablement changed.
OTA publishing remains held. Existing k=10, D7 grouping and same-session,
30-minute association semantics remain in the event schema documentation.

| Review comment | Disposition |
| --- | --- |
| P1 error handling | Done: ValueError skips quietly; unexpected write errors use constant logger.exception message and skip. No payload is passed to the logger. |
| P1 atomic write | Done: early lookup, matching and creation are inside transaction.atomic; post-save exception regression checks zero rows. |
| P2 ingest-time hints | Done: user-bound lookup retained; service contract and docs require report-time notif_id joins and same-user flag. |
| P2 client event time | Done: strict aware ISO timestamp, 24h/5m bounds, UTC storage, historical session suppression, report-time semantics documented. |
| P2 volume controls | Done: user/type/notif_id dedupe bounded to 14 days; prompt cap 10 per user/stage/trigger/UTC day, 2-day timeout, fail open; default 30/min. |
| P3 contradictory combinations | Done: cold_start/warm and none/opened rejected. |
| P3 early skip | Done: active type check before matching or view cache reads. |
| P3 content types | Done: JSONParser only. Disabled flag still returns skipped before parsing, preserving its existing behavior. |
| P3 Redis wording | Done: view failures skip; pre-view throttle/middleware failures may be 5xx; clients drop without hot retry. |
| P3 shared envelope | Done: both views use the same session and header validators; screen-view tests unchanged. |
| P3 shared throttle | Done: both inherit SettingsScopedRateThrottle; defaults only in settings.py. |
| P3 vocabularies | Done: named constants in event_schema.py. |
| P3 light views | Done: pure validator in event_schema.py; matching/recording in new services package. |

## Test hand-trace (not test execution)

Common setup: BaseTestCase creates core EventTypes once; tests use existing
registrations, never duplicate them. Each test has database rollback isolation,
LocMemCache cleared in setup/teardown, APIClient.force_authenticate (no cached
login sessions), and valid user fixtures. Lifecycle types require no target;
Event.save forces a constant title and clears IP/UA before full_clean. Every
insert below therefore passes model rules; lifecycle feed/cache side effects are
skipped by the merged #587 implementation. Mock paths name the imports actually
used by the service/view. API requests count toward the throttle even when no
row is written. Every ordinary test stays below the default 30-request budget;
the two throttle tests deliberately exhaust their respective budgets.

Method names below are in events/tests/test_notification_tracking.py.

| New/changed test | Hand-trace and expected outcome |
| --- | --- |
| test_invalid_payloads | Existing malformed bodies plus contradictory pairs fail pure validation before recording; 400 each, zero rows. List-valued vocabulary fields short-circuit before membership in sets/maps. |
| test_user_bound_matching_and_server_kind | Other-user send cannot match first open. Delete that open before second request to avoid new dedupe; own send matches and supplies daily_fast rather than forged kind. Destination is a separate type and matches. |
| test_optional_metadata_and_invalid_headers | Missing session is null; version with spaces and platform with uppercase/spaces fail shared regexes, yielding nulls. |
| test_telemetry_failures_are_skipped | Mock create_event, Event manager filter, then view session_cache_key separately. Each raises RuntimeError inside recording's atomic block, unwinds before response mapping, logs exact constant message, returns skipped; no lifecycle row survives. assertLogs captures logging rather than allowing noise. |
| test_scoped_throttle | Settings override 2/min is read dynamically. First open writes, second returns duplicate (still HTTP 200), third is throttled 429 before view. One row. |
| test_post_insert_failure_rolls_back | Real create_event/full_clean/INSERT runs. Connected Event post_save receiver raises only for opened; atomic block rolls INSERT back before view catches RuntimeError. Receiver disconnects in finally. skipped, zero rows, error log captured. |
| test_create_value_error_skips_without_logging | Active type exists; create_event mock raises ValueError before insert. Atomic unwinds, view skips without logger.exception; zero rows. |
| test_inactive_skips_before_matching_and_envelope | Existing opened type deactivated using update. Service checks EventType first and skips; Event manager and envelope mocks must remain uncalled. |
| test_json_only | Enabled authenticated requests use form/multipart media types. Accessing request.data invokes only JSONParser, yielding 415 before validation; no rows. |
| test_dedupe_user_type_and_window | Other user's opened row cannot block own open. Repeated own open duplicates; first destination writes independently, repeat duplicates. Two own rows; move own timestamps 15 days back and next open writes. |
| test_prompt_daily_cap_and_dimensions | Freeze UTC noon; first ten identical shown/settings prompts insert, eleventh caps. Different trigger, different stage/choice and different user each insert. Following UTC day inserts again, totaling 14 rows. Throttle timer is independent of patched timezone.now; 15 requests for original user is below 30. |
| test_prompt_cap_cache_failure_fails_open | Mock cache.add only (throttle uses get/set); cap catches RuntimeError, envelope cache read succeeds, prompt inserts. |
| test_prompt_increment_failure_fails_open | First prompt seeds counter. Second cache.add returns false, mocked incr fails, cap fails open; envelope and insert succeed. Two rows. |
| test_default_notification_throttle | Empty DRF rate map exercises settings fallback 30/min. First request inserts; following 29 deduplicate; request 31 gets 429; one row. |
| test_invalid_occurred_at_returns_400 | Null, junk, naive timestamp, 25h old and 6m ahead are invalid; all 400, no rows. |
| test_shared_header_vocabulary | desktop matches shared lowercase platform regex; version punctuation matches screen regex; both persisted, no raw headers copied. |
| test_occurred_at_normalization_and_session | Seed cached session; delete lifecycle rows between subcases so dedupe cannot mask checks. Freeze timezone.now shared by validator/service. Missing time and ages 10m/future 4m keep session; 31m/120m clear it. UTC values persist exactly; absent time omitted. |
| test_validation_allowlists_and_utc | SimpleTestCase forbids DB access. A +02:00 aware timestamp normalizes to frozen UTC now. Shown omits choice and unknown private field; exact data equality. |
| test_invalid_vocabulary_without_database | SimpleTestCase calls validator directly: nonobject, missing event, list entry, bad ID, contradictory open/destination and invalid prompt choice raise ValueError. Valid cold tap returns opened code without ORM access. |
| test_strict_time_validation_and_bounds | SimpleTestCase rejects wrong types, missing time/zone, invalid date/offset including offset minute 99, 24h+1s old and 5m+1s future. Exactly 24h old and 5m future pass under frozen clock. |

Unchanged notification tests were also traced: privacy test submits three distinct
types so dedupe does not interfere, exact data has no occurred_at when absent;
prompt combinations stay under each counter; 15-day sends cannot match;
flag-off exits before parsing/recording; missing/inactive cases skip without
counters/matching; unauthenticated requests fail before writes. All unchanged
screen-view tests retain their meaning: identical envelope regexes/cache lookup,
120/min fallback, dynamic override, same create_event call and ValueError path.
No screen-view test was edited.

## Checks

- `python3 -m py_compile bahk/settings.py events/event_schema.py events/views.py events/services/__init__.py events/services/notification_telemetry.py events/tests/test_notification_tracking.py` — passed (repeated after edits).
- `git diff --check` — passed (repeated after edits).
- `if command -v ruff >/dev/null 2>&1; then ruff check .; elif test -x .venv/bin/ruff; then .venv/bin/ruff check .; else echo 'SKIPPED: ruff executable unavailable'; fi` — reported skipped: no executable. No install attempted.
- Initial `python /tmp/update_ingestion.py` edit helper invocation failed because `python` is unavailable; reran with `python3` successfully. This was not a test invocation.
- Django SQLite suite and PostgreSQL concurrency job: skipped locally per brief; CI is the only runner. No Docker, Crabbox, dependency installation, migrations, pushes or PR operations performed.

## Remaining verification and limits

CI must execute the tests and lint. Dedupe is a bounded indexed existence check,
so simultaneous matching requests can both insert; no uniqueness guarantee is
claimed. Prompt cap is cache-based and counts attempts even if a later event write
rolls back. Cache backend atomic increment support determines concurrent cap
precision; failures intentionally fail open. Report-time joins/attribution are
PR7 work; this round documents that contract without adding reports.
