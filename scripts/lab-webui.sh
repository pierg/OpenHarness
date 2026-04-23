#!/usr/bin/env bash
# Lab web UI — thin convenience wrapper around the canonical CLIs.
#
# Foreground (development; Ctrl-C stops the server):
#   ./scripts/lab-webui.sh
#   ./scripts/lab-webui.sh --reload
#   ./scripts/lab-webui.sh --port 8080
#
# systemd --user units (persistent; install once: bash scripts/systemd/install.sh):
#   ./scripts/lab-webui.sh svc status
#   ./scripts/lab-webui.sh svc start web
#   ./scripts/lab-webui.sh svc stop web
#   ./scripts/lab-webui.sh svc restart web
#   ./scripts/lab-webui.sh svc logs web -f
#
# Equivalent Typer commands (from repo root):
#   uv run lab webui …
#   uv run lab svc …
#
# For daemon + web together, use `uv run lab svc …` with unit `all`, or
# `bash scripts/lab-svc.sh …`.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,24p' "$0" | sed 's/^# \?//'
  exit 0
fi

if [[ "${1:-}" == "svc" || "${1:-}" == "systemd" ]]; then
  shift
  exec uv run lab svc "$@"
fi

exec uv run lab webui "$@"
