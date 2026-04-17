#!/usr/bin/env bash
# Start any OpenHarness CLI command as a detached background job.
#
# Usage:
#   scripts/exp/start.sh <uv run args...>
#
# Examples:
#   scripts/exp/start.sh exec tb2-baseline
#   scripts/exp/start.sh exec tb2-baseline --profile smoke
#   scripts/exp/start.sh rerun tb2-baseline-smoke-20260416-205703

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/exp/_lib.sh
source "$SCRIPT_DIR/_lib.sh"

if [[ $# -eq 0 ]]; then
  CALL_DIR=$(dirname "$0")
  echo "Usage: $0 <uv run args...>"
  echo "Examples:"
  echo "  $CALL_DIR/start.sh exec tb2-baseline"
  echo "  $CALL_DIR/start.sh exec tb2-baseline --profile smoke"
  echo "  $CALL_DIR/start.sh rerun tb2-baseline-smoke-20260416-205703"
  exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux is not installed. Please install it first." >&2
  exit 1
fi

ACTION="$1"
TARGET="${2:-exp}"
TS=$(date +%Y%m%d-%H%M%S)

# Derive a clean session name
if [[ "$ACTION" == "rerun" ]]; then
    SESSION="${TARGET}-rerun"
else
    SESSION="${TARGET}-${TS}"
fi

# Clean up session name (tmux doesn't like dots or colons in session names)
SESSION=$(echo "$SESSION" | tr -C 'a-zA-Z0-9_-' '-')

load_env_file || echo "(no .env at root, relying on inherited env)"
require_provider_key

LOG="/tmp/${SESSION}.log"
CMD_QUOTED=$(printf '%q ' uv run "$@")

echo "==> Starting background job"
echo "    Session : $SESSION"
echo "    Command : uv run $*"
echo "    Log     : $LOG"

ROOT=$(repo_root)
# Wrap the command: tee to a log, and keep the shell open after it finishes
# so you can attach and read the final output/errors before it closes.
WRAPPED="cd $(printf '%q' "$ROOT") && ${CMD_QUOTED}2>&1 | tee $(printf '%q' "$LOG"); echo; echo '===> Job finished, exit=$?'; exec \"\${SHELL:-bash}\""

tmux new-session -d -s "$SESSION" "$WRAPPED"

CALL_DIR=$(dirname "$0")
cat <<EOF

OK. Job is running detached.

  List jobs  : $CALL_DIR/list.sh
  Attach     : $CALL_DIR/attach.sh $SESSION
  Stop job   : $CALL_DIR/stop.sh $SESSION
  Tail log   : tail -f $LOG
  Run status : $CALL_DIR/status.sh (once manifest is written)
EOF
