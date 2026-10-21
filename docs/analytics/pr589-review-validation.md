# PR589 review disposition and test hand-trace

Runtime validation is reserved for CI (SQLite Django suite and PostgreSQL
concurrency job). No Docker, installs, migrations, push or deployment performed.

## Comment disposition

| Comment | Disposition |
| --- | --- |
| P1 destination mapper | Done: shared ROUTE_FAMILIES fullmatch, URL and legacy families; all documented routes covered. |
| P1 rolling deploy | Done: explicit task kinds gated everywhere; signatures retain defaults; API tests cover both flag states. |
| P1 batching | Done: one active type lookup, ordered rows, bulk_create(batch_size=500) in caught atomic savepoint; explicit lifecycle fields/version. |
| P2 query load | Done: only id/token/device_type/user_id; no related User loading. |
| P2 correlatable | Done: true only on successful augmentation; false on oversized/preparation failure; contract/docs updated. |
| P2 result hygiene | Done: hashed invalid tokens and exception class labels; task/admin consumers count/join only. |
| P2 log hygiene | Done: constant messages, hashed tokens, exception classes. |
| P2 DeviceNotRegistered | Done: legacy branch unit test renamed; returned rejection test preserves active token and sent count; documented. |
| P3 error_code | Done: setdefault preserves specific exception code. |
| P3 headroom | Done: body/data JSON threshold 4096 - 512. |
| P3 hoisting | Done: kind validation, destination, flag and imports outside loop; single payload copy; ticket helper. |
| P3 default kind | Done: notification_type fallback; five redundant scheduled kwargs removed. |
| P3 caller audit | Done: recursive first-party app discovery via apps.py, excludes tests/migrations/venvs; validates discovered calls without fixed count. |
| P3 user-id log | Done: logs count only. |
| SDK pin | Intentionally not done: requires confirmed deployed version; documented. |
| Receipts/ingestion/reports/OTA | Intentionally not done: separate PRs/held work. |

D1/D2/D3/D4/D6/D7 preserved: no migration, dashboard isolation unchanged,
400-day retention intended only, same-session 30-minute association unchanged,
no new token disabling, reporting groups unchanged. Flag default remains false.

## Test hand-trace

All SendTelemetryTests inherit BaseTestCase, whose setUpClass pre-creates core
EventTypes. setUp creates a real user/token and patches the imported
notifications.utils.PushClient, so no publish reaches the network. No client
sessions or DummyCache settings are used. bulk_create bypasses Event.save and
full_clean; writer supplies lifecycle title, empty description/UA, null IP,
user_id and schema version explicitly, timestamp uses timezone.now default.

Each new/changed test was traced as follows:

| Test (SendTelemetryTests unless qualified) | Hand-trace |
| --- | --- |
| test_accepted_ticket | Active pre-created type found once; ok ticket sets accepted; augmented data and row share ID; correlatable true; privacy fields explicit. |
| test_success_warning_and_telemetry_logs_are_private | First send succeeds; second raises mocked legacy SDK exception; bulk_create raises ValueError inside caught savepoint; all logs contain constants/hash/class only. |
| test_legacy_device_not_registered_exception_branch | Mock explicitly raises legacy exception (not a claim about publish behavior); existing branch disables token, increments failed and returns hash; attached ticket retains specific error code. |
| test_send_exception_and_logs_are_private | RuntimeError yields failed=1, errors=['RuntimeError']; no attached ticket means send_error; bulk row contains no exception text. |
| test_telemetry_failures_do_not_change_counts | Each ValueError/IntegrityError is injected at Event.objects.bulk_create; atomic unwinds before catch; publish outcome counts remain 1/0 or 0/1 and no rows persist. |
| test_database_error_isolated_by_savepoint | Same actual bulk_create patch target raises IntegrityError within nested atomic; subsequent token query remains usable. |
| test_unknown_kind_platform_and_oversize_payload | Token platform updated to null; unrecognized kind becomes unclassified; oversized envelope returns original data, false correlation, still accepted outcome row. |
| test_destination_only_returns_families | Pure mapping covers every CLAUDE payload route, URL, params, legacy keys and malformed/unknown routes; fullmatch rejects trailing private segments. Bare learn now other per route contract. |
| test_all_production_callers_pass_allowlisted_kind | AST visits recursive app Python files; unwraps delay/apply_async; accepts explicit allowlisted kind, gated helper kind, task forwarding variable, or existing fourth-position notification_type; validates each discovered call. |
| test_preparation_failure_keeps_uncorrelatable_outcome | Envelope-only dumps failure occurs after ID allocation; original payload published, false correlation row inserted; JSONField serialization delegates to original dumps. |
| test_one_type_lookup_and_bulk_write_for_multiple_tokens | Two tokens selected; manager mocks wrap real filter/bulk_create, so both publishes execute and one write receives two ordered Event instances; filter called once with active send code. |
| test_notification_type_supplies_default_kind | culmination_feast belongs to allowlist and does not trigger preference filtering; default unclassified becomes culmination_feast in row. |
| TestPushNotificationTests.test_push_notification_success | Class flag override false; force_authenticate bypasses sessions; view's patched task gets message/user_ids and no kind. |
| TestPushNotificationTests.test_push_notification_no_token | Same flag-off path; view does not inspect tokens and still queues without kind. |
| TestPushNotificationTests.test_push_notification_default_message | Empty request chooses constant default message; flag-off helper supplies no kwargs. |
| TestPushNotificationTests.test_push_notification_telemetry_enabled | Method override true supersedes class false; patched name notifications.views.send_push_notification_task receives kind=test. |

Also traced retained coverage: returned DeviceNotRegistered error tickets are
non-raising, counted sent and recorded provider_rejected with specific code;
token remains active. Missing/inactive type skips all rows without suppressing
publishes. Flag-off sends keep original data. Distinct-token copies/IDs remain
independent. Missing/unknown tickets retain bounded outcome fields. Direct task
forwarding preserves kind because those calls execute in-process, not on old
workers. Test factories are not asked to create duplicate lifecycle types.

## Checks

- `command -v ruff`: unavailable (no executable).
- `ruff check .`: could not run, exit 127 (`command not found`). No install attempted.
- `python3 -m py_compile notifications/utils.py notifications/tasks.py notifications/admin.py notifications/views.py prayers/tasks.py prayers/views.py events/signals.py events/event_schema.py notifications/tests/test_send_telemetry.py notifications/tests/test_device_tokens.py`: passed.
- `git diff --check`: passed.
- Django SQLite/PostgreSQL suites, Crabbox and Docker: skipped by explicit CI-only instruction.

Remaining limits: runtime CI has not run locally; deployed SDK version is unknown;
retention enforcement, receipts and report denominators remain future work.

The caller audit was additionally executed as an isolated static check (passed),
without importing Django or running a runtime suite. Exact command:

```bash
python3 - <<'PY'
import ast
from pathlib import Path
import unittest
schema = ast.parse(Path('events/event_schema.py').read_text())
kinds = next(ast.literal_eval(n.value.args[0]) for n in schema.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NOTIFICATION_KINDS' for t in n.targets))
path = Path('notifications/tests/test_send_telemetry.py').resolve()
tree = ast.parse(path.read_text())
method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'test_all_production_callers_pass_allowlisted_kind')
namespace = {'ast': ast, 'Path': Path, '__file__': str(path), 'NOTIFICATION_KINDS': kinds}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
namespace[method.name](unittest.TestCase())
print('Static production caller audit passed')
PY
```

`git status --short` after the implementation commit: clean.
