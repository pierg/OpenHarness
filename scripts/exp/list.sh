#!/usr/bin/env bash
# List active background jobs (tmux sessions).

set -euo pipefail

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed."
  exit 0
fi

echo "Active Jobs:"
if ! tmux ls 2>/dev/null | sed 's/^/  /'; then
  echo "  (none)"
fi
