#!/usr/bin/env bash
# SessionStart hook for Claude Code cloud sessions (claude.ai/code).
#
# Cloud VMs have no docker-compose dev container, so this builds a Python 3.11
# virtualenv in .venv, starts the preinstalled Redis, and exports the env vars
# settings.py needs. It is a no-op everywhere else (CLAUDE_CODE_REMOTE unset).
# See docs/CLOUD_ENVIRONMENT.md.
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"
ROOT="$(pwd)"
VENV="$ROOT/.venv"
STAMP="$VENV/.requirements.sha256"
LOG="$ROOT/.venv-setup.log"

# Install output goes to a log; stdout from this hook is added to Claude's context.
{
  if [ ! -x "$VENV/bin/python" ]; then
    if command -v python3.11 >/dev/null 2>&1; then
      uv venv --python "$(command -v python3.11)" "$VENV"
    else
      # No system 3.11 (setup script skipped?): let uv fetch a managed one.
      uv venv --python 3.11 "$VENV"
    fi
  fi

  want="$(sha256sum requirements.txt | cut -d' ' -f1)"
  if [ "$(cat "$STAMP" 2>/dev/null || true)" != "$want" ]; then
    uv pip install --python "$VENV/bin/python" -r requirements.txt
    echo "$want" > "$STAMP"
  fi
} >"$LOG" 2>&1 || {
  echo "Cloud setup FAILED building .venv; see $LOG"
  tail -n 20 "$LOG"
  exit 0
}

# Redis is preinstalled but not running. Tests don't need it (locmem cache,
# eager Celery); runserver and celery do.
redis-cli ping >/dev/null 2>&1 \
  || service redis-server start >/dev/null 2>&1 \
  || redis-server --daemonize yes >/dev/null 2>&1 \
  || echo "Warning: could not start redis-server"

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  cat >>"$CLAUDE_ENV_FILE" <<EOF
export VIRTUAL_ENV="$VENV"
export PATH="$VENV/bin:\$PATH"
export IS_PRODUCTION="\${IS_PRODUCTION:-false}"
# settings.py defaults to the docker-compose host "redis", which doesn't exist here.
export REDIS_URL="\${REDIS_URL:-redis://localhost:6379/1}"
# Placeholders for settings with no default. Real keys belong in the cloud
# environment's Network secrets, never here.
export MAILGUN_DOMAIN="\${MAILGUN_DOMAIN:-example.test}"
export MAILGUN_API_KEY="\${MAILGUN_API_KEY:-dev-placeholder}"
export OPENAI_API_KEY="\${OPENAI_API_KEY:-dev-placeholder}"
export ANTHROPIC_API_KEY="\${ANTHROPIC_API_KEY:-dev-placeholder}"
EOF
fi

echo "Cloud session: run commands directly with .venv (Python $("$VENV/bin/python" -V 2>&1 | cut -d' ' -f2)), not via docker exec. Tests: python manage.py test --parallel --exclude-tag=performance --settings=tests.test_settings"
