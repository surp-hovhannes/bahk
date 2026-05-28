#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/.crabbox.slug.conf"
LOCAL_ENV_FILE="$ROOT_DIR/.crabbox.env"
STATE_DIR="$ROOT_DIR/.crabbox"
STATE_FILE="$STATE_DIR/active-lease.env"
LOCK_DIR="$STATE_DIR/warmup.lock"

usage() {
  printf 'Usage: %s {warm|status|stop|slug}\n' "$0" >&2
}

shell_quote() {
  printf "%q" "$1"
}

load_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    printf 'Missing %s\n' "$CONFIG_FILE" >&2
    exit 1
  fi

  set -a
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
  if [[ -f "$LOCAL_ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$LOCAL_ENV_FILE"
  fi
  set +a

  : "${CRABBOX_SLUG:?CRABBOX_SLUG is required}"
  : "${CRABBOX_IDLE_TIMEOUT:?CRABBOX_IDLE_TIMEOUT is required}"
  : "${CRABBOX_TTL:?CRABBOX_TTL is required}"
  : "${CRABBOX_CLASS:?CRABBOX_CLASS is required}"
}

load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$STATE_FILE"
  fi
}

active_ref() {
  load_state
  if [[ -z "${CRABBOX_ACTIVE_LEASE_ID:-}" ]]; then
    printf 'No active Crabbox lease recorded. Run %s warm first.\n' "$0" >&2
    exit 1
  fi
  printf '%s\n' "$CRABBOX_ACTIVE_LEASE_ID"
}

active_status_ok() {
  local lease_id
  lease_id="$(active_ref)"
  crabbox status --id "$lease_id" >/dev/null 2>&1
}

write_state() {
  local lease_id="$1"
  local concrete_slug="$2"

  mkdir -p "$STATE_DIR"
  {
    printf 'CRABBOX_REQUESTED_SLUG=%s\n' "$(shell_quote "$CRABBOX_SLUG")"
    printf 'CRABBOX_ACTIVE_LEASE_ID=%s\n' "$(shell_quote "$lease_id")"
    printf 'CRABBOX_ACTIVE_SLUG=%s\n' "$(shell_quote "$concrete_slug")"
  } > "$STATE_FILE"
}

acquire_lock() {
  mkdir -p "$STATE_DIR"
  local waited=0
  until mkdir "$LOCK_DIR" 2>/dev/null; do
    if (( waited >= 300 )); then
      printf 'Timed out waiting for Crabbox warmup lock: %s\n' "$LOCK_DIR" >&2
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
}

parse_lease_id() {
  grep -Eo 'cbx_[[:alnum:]]+' | tail -n 1
}

parse_slug() {
  sed -nE 's/.*slug[ =]([[:alnum:]_.-]+).*/\1/p' | tail -n 1
}

warm() {
  load_config
  mkdir -p "$STATE_DIR"
  acquire_lock

  if [[ -f "$STATE_FILE" ]] && active_status_ok; then
    load_state
    printf 'Using Crabbox lease %s (%s)\n' "$CRABBOX_ACTIVE_LEASE_ID" "${CRABBOX_ACTIVE_SLUG:-$CRABBOX_ACTIVE_LEASE_ID}" >&2
    return
  fi

  rm -f "$STATE_FILE"

  local output lease_id concrete_slug
  if ! output="$(
    crabbox warmup \
      --slug "$CRABBOX_SLUG" \
      --idle-timeout "$CRABBOX_IDLE_TIMEOUT" \
      --ttl "$CRABBOX_TTL" \
      --class "$CRABBOX_CLASS" 2>&1
  )"; then
    printf '%s\n' "$output" >&2
    exit 1
  fi

  printf '%s\n' "$output" >&2
  lease_id="$(printf '%s\n' "$output" | parse_lease_id)"
  concrete_slug="$(printf '%s\n' "$output" | parse_slug)"

  if [[ -z "$lease_id" ]]; then
    printf 'Could not parse Crabbox lease id from warmup output.\n' >&2
    exit 1
  fi

  if [[ -z "$concrete_slug" ]]; then
    concrete_slug="$lease_id"
  fi

  write_state "$lease_id" "$concrete_slug"
}

status() {
  load_config
  crabbox status --id "$(active_ref)"
}

stop_box() {
  load_config
  local lease_id
  lease_id="$(active_ref)"
  crabbox stop "$lease_id"
  rm -f "$STATE_FILE"
}

slug() {
  load_state
  if [[ -z "${CRABBOX_ACTIVE_LEASE_ID:-}" ]]; then
    printf 'No active Crabbox lease recorded. Run %s warm first.\n' "$0" >&2
    exit 1
  fi
  printf '%s\n' "${CRABBOX_ACTIVE_SLUG:-$CRABBOX_ACTIVE_LEASE_ID}"
}

case "${1:-}" in
  warm)
    warm
    ;;
  status)
    status
    ;;
  stop)
    stop_box
    ;;
  slug)
    slug
    ;;
  *)
    usage
    exit 2
    ;;
esac
