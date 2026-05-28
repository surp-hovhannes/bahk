#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB="${1:-}"
LOCAL_ENV_FILE="$ROOT_DIR/.crabbox.env"

usage() {
  printf 'Usage: %s {ci|lint|typecheck|test|coverage|e2e}\n' "$0" >&2
}

case "$JOB" in
  ci|lint|typecheck|test|coverage|e2e)
    ;;
  *)
    usage
    exit 2
    ;;
esac

"$ROOT_DIR/scripts/crabbox-box.sh" warm
LEASE_REF="$("$ROOT_DIR/scripts/crabbox-box.sh" slug)"

if [[ -f "$LOCAL_ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$LOCAL_ENV_FILE"
  set +a
fi

crabbox job run --id "$LEASE_REF" --stop never "$JOB"
