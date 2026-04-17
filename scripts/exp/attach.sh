#!/usr/bin/env bash
# Attach to an active background job to view its live progress.
#
# Usage:
#   scripts/exp/attach.sh                  # auto-attaches if only 1 job is active
#   scripts/exp/attach.sh <session_name>   # attaches to specific job

set -euo pipefail

if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux is not installed" >&2
  exit 1
fi

SESSION="${1:-}"

# Auto-select if no session provided
if [[ -z "$SESSION" ]]; then
  SESSIONS_COUNT=$(tmux ls 2>/dev/null | wc -l | tr -d ' ' || echo 0)
  
  if [[ "$SESSIONS_COUNT" -eq 0 ]]; then
    echo "No active jobs to attach to."
    exit 1
  elif [[ "$SESSIONS_COUNT" -eq 1 ]]; then
    SESSION=$(tmux ls -F '#S' 2>/dev/null)
    echo "Auto-attaching to the only active job: $SESSION"
  else
    CALL_DIR=$(dirname "$0")
    echo "Usage: $0 <session_name>"
    echo ""
    "$CALL_DIR/list.sh"
    exit 1
  fi
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "ERROR: no active job named '$SESSION'." >&2
  exit 1
fi

echo "Attaching... (Press Ctrl-b then d to detach and leave it running in background)"
sleep 1.5
exec tmux attach -t "$SESSION"
