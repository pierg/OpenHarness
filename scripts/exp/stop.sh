#!/usr/bin/env bash
# Stop an active background job.
#
# Usage:
#   scripts/exp/stop.sh <session_name>

set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "Usage: scripts/exp/stop.sh <session_name>"
  echo ""
  scripts/exp/list.sh
  exit 1
fi

SESSION="$1"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "ERROR: No active job named '$SESSION'."
  exit 1
fi

tmux kill-session -t "$SESSION"
echo "OK. Stopped job '$SESSION'."
