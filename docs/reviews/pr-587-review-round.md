# PR #587 review round validation

Runtime tests were hand-traced, not executed. The requested CI SQLite suite and
PostgreSQL concurrency job remain required. No installs, Docker, migrations,
model field changes, push, or PR operations were performed.

## Test hand-traces

All tests in `events/tests/test_notification_lifecycle.py` inherit BaseTestCase,
which registers all core types before each class. TestCase rolls each test back.
The class uses DummyCache and synchronous feeds by default. APIClient uses DRF
force_authenticate, never session login. Lifecycle types require no target;
APP_OPEN, CHECKLIST_USED and SCREEN_VIEW also require no target. Explicit titles
are supplied for ordinary events; lifecycle save sets a constant before full_clean.

| Test | Hand-trace and expected result |
| --- | --- |
| registration_defaults_and_existing_rows_are_preserved | Delete only lifecycle types, recreate exactly five with notification/false/false defaults. APP_OPEN retains its default analytics flag. Reinitialization preserves an existing disabled, analytics-enabled row. |
| classification_and_payload_contracts | All five codes have payload contracts, are exposure-noise, and are disjoint from action/meaningful sets. Payload keys exclude unbounded content keys. |
| vocabulary_and_reporting_groups | Import notification preferences inside the test, avoiding production import cycles. All preference keys are allowed kinds, post_fast is absent, reporting keys are sorted, and only the two prayer kinds share a group. |
| shared_aggregates_and_activity_feed_exclude_lifecycle | Corrupt lifecycle category to analytics and enable analytics flags via update (no model validation needed). Five lifecycle events remain excluded by type ID. APP_OPEN, CHECKLIST_USED and v2 true-UI SCREEN_VIEW remain; legacy screen is excluded. None of these codes produce feed rows. SQL has no JOIN to events_eventtype, including the participation-noop subquery. |
| check_missing_is_read_only_then_initialization_succeeds | Delete notification_send from pre-created core types. Read-only check raises CommandError and preserves rows. Recreate defaults, then check succeeds without writes. |
| check_inactive_is_read_only | Disable APP_OPEN only; check prints inactive information and returns normally, preserving all rows. |
| documented_routes_have_non_fallback_families | Resolve CLAUDE.md from repository root; extract every screen example in its push section. Split each into family/tail, require full regex match and normalized destination membership. Top-level URL example requires url membership. |
| lifecycle_titles_and_request_metadata_are_private | Use an email username and a RequestFactory request with IP/UA. For each of five codes, omitted and free-text titles both become exact constants. Default description is empty; neither identity string appears. IP/UA remain absent. |
| check_attributes_and_force_preserve_off_switch_and_other_types | Independently corrupt each lifecycle attribute using update, leaving the row disabled. Each check raises and reports its field without writes. Force repairs only lifecycle attributes; check then succeeds with inactive info. APP_OPEN's entire row remains unchanged. Combined check/force raises before writes. |
| lifecycle_skips_save_side_effects_other_types_keep_them | Create user before installing mocks. Under async feeds, five valid lifecycle saves invoke neither invalidation nor delay. One titled APP_OPEN invokes each exactly once. |
| my_events_hides_lifecycle | Create notification_send and APP_OPEN for one user. Force-authenticated GET /api/events/events/my/ returns 200; handle paginated or unpaginated results and require APP_OPEN ID but no notification_send ID. |
| lifecycle_skips_synchronous_feed_dispatch | Under class default synchronous setting, notification_send never calls create_from_event; APP_OPEN calls it exactly once with event/user. |
| backfills_skip_lifecycle_before_feed_existence_checks | Create only notification_send for user. Direct invocation of the Celery task runs its body, finds user, and excludes telemetry before iteration, returning zero. Management command with user_id excludes the same row. Mocked feed manager filter is never called. |

## Deliberately untouched

D1 data migration alternative: no action, per decision. Registration remains a
deploy step. `events/growth_cohorts.py` freshness belongs to #586. Retention remains
an unenforced 400-day intention, attribution remains same-session/30-minute
association, and OTA publishing remains held. Public event names remain stable.
