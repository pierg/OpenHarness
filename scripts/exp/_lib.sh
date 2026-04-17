#!/usr/bin/env bash
# Shared helpers for scripts/exp/*. Source me; do not execute directly.
#
# Provides:
#   repo_root          - absolute path to the git toplevel
#   load_env_file      - source .env (or $ENV_FILE) into the current shell
#   require_provider_key - exit 1 unless one model provider key is set
#   find_latest_run    - print the newest matching runs/experiments/<prefix>-* dir
#   resolve_run        - turn an instance id / prefix / path into an absolute dir
#
# All helpers are macOS-friendly (BSD coreutils).

set -uo pipefail

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null
}

load_env_file() {
  local env_file="${ENV_FILE:-${1:-.env}}"
  if [[ -f "$env_file" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$env_file"; set +a
    return 0
  fi
  return 1
}

require_provider_key() {
  if [[ -z "${GOOGLE_API_KEY:-}${GEMINI_API_KEY:-}${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}" ]]; then
    echo "ERROR: no model provider key set in environment" >&2
    echo "       expected one of: GOOGLE_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY" >&2
    return 1
  fi
}

# find_latest_run [prefix]
#   prefix defaults to "tb2-baseline". Skips smoke profile dirs unless
#   $INCLUDE_SMOKE=1 because the common case is "show me the real run".
find_latest_run() {
  local prefix="${1:-tb2-baseline}"
  local glob="runs/experiments/${prefix}-*"
  local include_smoke="${INCLUDE_SMOKE:-0}"

  # ls -1dt sorts newest-first by mtime; suppress "no matches" stderr.
  local matches
  if ! matches=$(ls -1dt $glob 2>/dev/null); then
    return 1
  fi

  while IFS= read -r dir; do
    [[ -z "$dir" ]] && continue
    if [[ "$include_smoke" != "1" && "$dir" == *"-smoke-"* ]]; then
      continue
    fi
    echo "$dir"
    return 0
  done <<< "$matches"

  return 1
}

# resolve_run <arg>
#   <arg> may be:
#     - a path under runs/experiments/ (returned as-is if it exists)
#     - an absolute path (returned as-is if it exists)
#     - an instance id like tb2-baseline-20260416-211530
#     - the literal "smoke" → latest -smoke- run of default experiment
#     - a prefix like tb2-baseline (resolves to newest matching, non-smoke
#       unless $INCLUDE_SMOKE=1)
#   empty <arg> falls back to "tb2-baseline" prefix.
resolve_run() {
  local arg="${1:-tb2-baseline}"

  if [[ -d "$arg" ]]; then
    echo "$arg"
    return 0
  fi

  local candidate="runs/experiments/$arg"
  if [[ -d "$candidate" ]]; then
    echo "$candidate"
    return 0
  fi

  # "smoke" shortcut: latest -smoke- run of the default experiment.
  if [[ "$arg" == "smoke" ]]; then
    INCLUDE_SMOKE=1 find_latest_smoke_run "tb2-baseline"
    return $?
  fi

  find_latest_run "$arg"
}

# find_latest_smoke_run [prefix]
#   Like find_latest_run but selects the newest <prefix>-smoke-* dir.
find_latest_smoke_run() {
  local prefix="${1:-tb2-baseline}"
  local glob="runs/experiments/${prefix}-smoke-*"
  local matches
  if ! matches=$(ls -1dt $glob 2>/dev/null); then
    return 1
  fi
  echo "$matches" | head -1
}
