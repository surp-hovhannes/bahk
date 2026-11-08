# Issue 605: Expo transport recovery

The send loop now reports a bounded vocabulary in `transport_errors`: operation
`publish`, classification (`permanent_reject`, `transient_reject`, or
`ambiguous_acceptance`), numeric HTTP status or null, and attempt number. This
additive result field and transport logs contain neither tokens nor response text.
Existing lifecycle event schemas and legacy sent/failed counts remain unchanged.
A transport exception remains `send_error`, even when a request-level rejection
is known; `provider_rejected` continues to describe ticket-level rejection.

Only a structured whole-request Expo error response without `data`, with HTTP
429 or 5xx, permits a retry. Three attempts maximum, sleeping 1 then 2 seconds;
10-second request timeout per attempt. Token activity and the original preference
filter are rechecked after each wait. A successful publish exits recovery before
local bookkeeping. Returned tickets, ticket exceptions, ambiguous transport
failures, mixed error/ticket responses and already accepted devices are never
replayed by this recovery loop. Receipt collection remains independent.

## Investigation

[Expo's send documentation](https://docs.expo.dev/push-notifications/sending-notifications/)
describes a whole-request failure as HTTP 4xx/5xx with an `errors` array, and
recommends backoff for 429/5xx. This implementation conservatively requires the
structured rejection as well as the transient status. A bare HTTP 503 may come
from an intermediary after acceptance; it does not prove safe replay.

The pinned dependency is `exponent_server_sdk==2.2.0`. Mocked HTTP sessions exercise
its actual parser: JSON request-level errors raise `PushServerError` carrying
`response` and `response_data`; non-JSON errors invoke `raise_for_status` and can
raise Requests `HTTPError`. Requests responses with error statuses are falsey;
status extraction therefore checks the response directly without truthiness.
The SDK's `publish` does not validate returned error tickets automatically.

Issue evidence identifies YV as 503; YZ's eight HTTPError events have no verified
status in the issue. No Sentry connector was available, so YZ status is still
unknown. No production events or provider sends were accessed.

## Risks and rollback

Ambiguous sends can be lost, because replay risks duplicate user notifications.
There is no cross-task idempotency guarantee: an independently redelivered Celery
task can still duplicate a send. Synchronous retries add up to three seconds of
backoff per affected device plus request time; a widespread outage may lengthen
a batch substantially. Request timeouts are Requests timeouts, not a hard total
wall-clock deadline. A future provider contract or SDK parser change needs review.

Rollback: revert the implementation commit. There are no schema, migration,
configuration or data changes to undo. Existing receipt tickets remain readable.

## Validation

Focused commands and outcomes are recorded below. Full CI, full-suite tests,
repository-wide lint/format and performance/slow tests are intentionally deferred
per the task's focused-check scope.

Git preparation checks:

- `pwd` — required isolated worktree path.
- `git status --short --branch` — clean initially, requested branch.
- `git rev-parse HEAD` — `a1a5d84dbdf40db8e4ba954503d4d4fb4f3bfa2b` initially.
- `git remote -v` — canonical origin for fetch and push.
- `git fetch --prune origin` — passed after sandbox escalation (initial sandbox
  attempt could not write the shared Git worktree metadata).
- `git rev-parse origin/main` — same required SHA. Branch already existed at
  this base on arrival; no branch recreation or history rewrite performed.
- `git diff --check` — passed during implementation and final review.

Environment setup and failed attempts:

- `command -v crabbox` — `/opt/homebrew/bin/crabbox`.
- `scripts/crabbox-box.sh warm` — passed; warmed isolated validation lease.
- `scripts/crabbox-box.sh status` — sandbox DNS failure; elevated remote runs
  subsequently reached the same lease successfully.
- `gh issue view 605 --repo surp-hovhannes/bahk --json title,body,comments` —
  sandbox network failure; issue retrieved using the connected GitHub tool.
- Initial `crabbox run` with `--stop never` failed argument parsing; that flag
  is supported for jobs, not this CLI's `run`. Removed it; explicit lease reused.
- First remote build/test attempt failed before checks: `sudo: docker: command
  not found`. Installed Docker using the same bootstrap as repo jobs:
  `sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2`.
- `sudo docker compose -f .devcontainer/docker-compose.yml up -d --build redis app`
  — passed; only isolated test services.

Focused checks run inside the app container (via
`crabbox run --id "$(scripts/crabbox-box.sh slug)" --shell '…'`):

```sh
sudo docker compose -f .devcontainer/docker-compose.yml exec -T -e IS_PRODUCTION=false -e SENTRY_DSN= app python manage.py test notifications.tests.test_transport notifications.tests.test_send_telemetry notifications.tests.test_device_tokens notifications.tests.test_receipts --noinput --settings=tests.test_settings
sudo docker compose -f .devcontainer/docker-compose.yml exec -T app ruff check notifications/utils.py notifications/tests/test_transport.py
```

First run: 76 tests passed; changed-file Ruff passed. Final run includes additional
edge cases (results below).

Installed SDK inspection command:

```sh
sudo docker compose -f .devcontainer/docker-compose.yml exec -T app python -c "import inspect, importlib.metadata, exponent_server_sdk as sdk; print(importlib.metadata.version(\"exponent_server_sdk\")); print(inspect.getsource(sdk.PushClient._publish_internal)); print(inspect.getsource(sdk.PushClient.publish))"
```

The expanded 81-test run initially had one fixture error: the new preference
revocation test assumed a profile existed. The fixture now explicitly creates
`hub.models.Profile`; all other 80 tests passed. Lint and SDK inspection were
skipped by that failed command's `&&` chain and are rerun after the fixture fix.

Final result after fixture fix: **81 tests passed** in 15.209 seconds; **changed-file
Ruff passed**; installed SDK inspection passed and confirmed version **2.2.0**
and the exception paths described above. Crabbox run
`run_9c28e4d7db0a529b7f022feb9a8c1561` exited 0. Only documentation changed after
this final runtime validation. Final `git diff --check` passed.
