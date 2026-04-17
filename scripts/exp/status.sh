#!/usr/bin/env bash
# Print a quick status snapshot for an experiment run.
#
# Usage:
#   scripts/exp/status.sh                              # latest non-smoke tb2-baseline run
#   scripts/exp/status.sh smoke                        # latest smoke run (just a prefix)
#   scripts/exp/status.sh tb2-baseline-20260416-...    # explicit instance id
#   scripts/exp/status.sh runs/experiments/<dir>       # explicit path
#
# Env knobs:
#   INCLUDE_SMOKE=1   include -smoke- runs in "latest" lookup

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/exp/_lib.sh
source "$SCRIPT_DIR/_lib.sh"

ROOT=$(repo_root) || { echo "ERROR: not a git repo" >&2; exit 1; }
cd "$ROOT"

ARG="${1:-tb2-baseline}"
RUN_DIR=$(resolve_run "$ARG") || {
  echo "ERROR: no experiment run found matching '$ARG'" >&2
  echo "       try: ls -1dt runs/experiments/" >&2
  exit 1
}

echo "================================================================"
echo "  $RUN_DIR"
echo "================================================================"

# Manifest-level status (delegates to the official CLI command).
if [[ -f "$RUN_DIR/experiment.json" ]]; then
  uv run status "$RUN_DIR" || true
else
  echo "(no experiment.json yet — run probably hasn't written its manifest)"
fi

echo
echo "--- per-leg progress ---"
shopt -s nullglob
LEG_DIRS=("$RUN_DIR"/legs/*/)
shopt -u nullglob
if (( ${#LEG_DIRS[@]} == 0 )); then
  echo "(no legs/ subdirectories yet)"
else
  printf '  %-22s %-10s %-12s  %s\n' "LEG" "STATUS" "TRIALS_DONE" "LATEST_ACTIVITY"
  for leg in "${LEG_DIRS[@]}"; do
    leg_id=$(basename "${leg%/}")
    leg_json="$leg/leg.json"

    if [[ -f "$leg_json" ]]; then
      status=$(uv run python -c "import json,sys; print(json.load(open(sys.argv[1])).get('status','?'))" "$leg_json" 2>/dev/null || echo '?')
    else
      status="(no leg.json)"
    fi

    # Count completed trial result.json files under this leg.
    # Trial layout: legs/<leg>/harbor/<job>/<task_slug>/result.json (depth 4),
    # so we skip the leg-level rollup at depth 3.
    n_trials=$(find "$leg" -mindepth 4 -maxdepth 5 -name result.json -type f 2>/dev/null | wc -l | tr -d ' ')

    # Most-recently-modified messages.jsonl as a "what's the agent doing" hint.
    latest=$(find "$leg" -name messages.jsonl -type f 2>/dev/null \
      | xargs -I{} stat -f "%m %N" {} 2>/dev/null \
      | sort -rn | head -1 | cut -d' ' -f2- || true)
    if [[ -n "$latest" ]]; then
      ts=$(stat -f "%Sm" -t "%H:%M:%S" "$latest" 2>/dev/null || echo "?")
      lines=$(wc -l < "$latest" | tr -d ' ')
      hint="$(basename "$(dirname "$latest")") ${lines}msg @${ts}"
    else
      hint="-"
    fi

    printf '  %-22s %-10s %-12s  %s\n' "$leg_id" "$status" "$n_trials" "$hint"
  done
fi

# If the run has produced rolled-up results, show them.
SUMMARY="$RUN_DIR/results/summary.md"
if [[ -f "$SUMMARY" ]]; then
  echo
  echo "--- results/summary.md ---"
  cat "$SUMMARY"
fi

# Recent retry / 429 frequency from events.jsonl - useful sanity check.
echo
echo "--- recent retry / rate-limit signals (last 20) ---"
RETRY_HITS=$(grep -hE '"(429|503|RetryableError|Gemini request failed)"|"retrying in' \
  "$RUN_DIR"/legs/*/harbor/*/*/events.jsonl 2>/dev/null | tail -20 || true)
if [[ -n "$RETRY_HITS" ]]; then
  echo "$RETRY_HITS"
else
  echo "(none)"
fi

# tmux session hint.
if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${TMUX_SESSION:-tb2}" 2>/dev/null; then
  echo
  echo "tmux session '${TMUX_SESSION:-tb2}' is live — reattach with: scripts/exp/attach.sh"
fi
