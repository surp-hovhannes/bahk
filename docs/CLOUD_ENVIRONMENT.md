# Claude Code cloud environment

How to run bahk in Claude Code cloud sessions (claude.ai/code). A cloud VM is
Ubuntu 24.04 with Python 3.12, uv, Node, Redis 7 and PostgreSQL 16 preinstalled
(the services are not running). There is no `bahk_devcontainer-app-1` container,
so commands run directly from a `.venv`, not through `docker exec`.

## What's in the repo

- `scripts/cloud-session-start.sh` — SessionStart hook. Only runs when
  `CLAUDE_CODE_REMOTE=true`, so local sessions are unaffected. It:
  - builds `.venv` on Python 3.11 with uv (skipped when the `requirements.txt` hash is unchanged)
  - starts Redis
  - writes `IS_PRODUCTION=false`, `REDIS_URL=redis://localhost:6379/1`, and placeholder
    Mailgun/OpenAI/Anthropic keys to the session env (only when they're unset)
  - logs install output to `.venv-setup.log`
- `.claude/settings.json` — registers that hook for `startup|resume`.

## One-time setup in claude.ai/code

Open the environment selector under the message box, add an environment (or edit one),
and set:

**Network access:** Trusted. PyPI, npm, Ubuntu apt and Launchpad PPAs are on the default allowlist.

**Setup script.** It runs as root before Claude starts, and the result is snapshotted, so
the slow installs happen once rather than every session:

```bash
#!/bin/bash
set -e
# Python 3.11 to match CI and .python-version (Ubuntu 24.04 ships 3.12).
if ! command -v python3.11 >/dev/null; then
  apt-get update
  apt-get install -y software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update
  apt-get install -y python3.11 python3.11-venv python3.11-dev
fi
# Best-effort: pre-build .venv so it lands in the snapshot. The hook builds it
# at session start if this step finds nothing.
repo="$(dirname "$(find / -maxdepth 6 -path '*/scripts/cloud-session-start.sh' -not -path '/proc/*' 2>/dev/null | head -1)")/.."
if [ -f "$repo/requirements.txt" ]; then
  CLAUDE_CODE_REMOTE=true CLAUDE_PROJECT_DIR="$repo" bash "$repo/scripts/cloud-session-start.sh" || true
fi
```

**Environment variables:** none are required. Tests use `tests/test_settings.py`
(in-memory SQLite, locmem cache, eager Celery). Any you do add are visible to everyone
who uses the environment, so use only dev values (for example `DEBUG=true`).

**Secrets:** real keys (`BIBLE_API_KEY`, `OPENAI_API_KEY`, Mailgun, AWS) never go in env
vars, the setup script or the repo. If a session needs to call a real API, add the key
under **Network secrets** for that API's host. Tests don't need real keys.

## Commands in a cloud session

```bash
ruff check .
python manage.py check
python manage.py test --parallel --exclude-tag=performance --settings=tests.test_settings
```

`--keepdb` does nothing here, because the test database is in memory.

## Troubleshooting

- **Hook says "Cloud setup FAILED"**: read `.venv-setup.log`. A common cause is no Python
  3.11: the setup script was skipped, and uv couldn't download a managed 3.11 either.
- **Dependencies changed**: the hook reinstalls on the next session start, when the
  `requirements.txt` hash changes. To do it mid-session, run
  `uv pip install -r requirements.txt`.
- **Postgres-only tests** (`tests.postgres_test_settings`): run `service postgresql start`
  first, then create the role and database that settings file expects.
