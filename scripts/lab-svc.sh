#!/usr/bin/env bash
# Operator wrapper for the lab services (auxiliary).
#
# The CANONICAL operator entry point is `lab svc` — a real Typer
# subcommand of the `lab` CLI (see src/openharness/lab/svc_cli.py).
# This bash script is kept only for non-Python contexts where pulling
# uv in is awkward (SSH ForceCommand, CI bootstrap, recovery from a
# broken venv).
#
# Subcommands mirror the Typer surface 1:1:
#   lab-svc.sh status                  # everything at a glance (default)
#   lab-svc.sh start    [unit]         # start one unit, or all
#   lab-svc.sh stop     [unit]
#   lab-svc.sh restart  [unit]
#   lab-svc.sh logs     [unit] [-f]    # journalctl -n 100 [--follow]
#   lab-svc.sh tail     [unit]         # alias for `logs <unit> -f`
#   lab-svc.sh url                     # print the web UI URL
#   lab-svc.sh install                 # run scripts/systemd/install.sh
#
# `unit` is one of `web`, `daemon`, or `all` (default). Short
# aliases — anything not matching is rejected so a typo can't
# accidentally target a different systemd unit on this host.

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEBUI_UNIT="openharness-lab"
DAEMON_UNIT="openharness-daemon"
WEBUI_PORT="${LAB_WEBUI_PORT:-8765}"
WEBUI_HOST="${LAB_WEBUI_HOST:-127.0.0.1}"

# ANSI helpers — auto-disable when stdout isn't a TTY so logs stay clean.
if [[ -t 1 ]]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
  C_DIM=$'\033[90m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_BOLD=""; C_RESET=""
fi

die()  { echo "${C_ERR}error:${C_RESET} $*" >&2; exit 1; }
warn() { echo "${C_WARN}warn:${C_RESET}  $*" >&2; }

# ---------------------------------------------------------------------------
# Unit resolution
# ---------------------------------------------------------------------------

resolve_unit() {
  # Map short names → systemd unit ids. Defaults to "all" so most
  # commands operate on both units when no arg is given.
  case "${1:-all}" in
    web|webui|ui)         echo "$WEBUI_UNIT" ;;
    daemon|d|orch)        echo "$DAEMON_UNIT" ;;
    all|both)             echo "$WEBUI_UNIT $DAEMON_UNIT" ;;
    *) die "unknown unit '$1' (use: web | daemon | all)" ;;
  esac
}

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

unit_pid()        { systemctl --user show "$1.service" -p MainPID --value 2>/dev/null || true; }
unit_active()     { systemctl --user is-active "$1.service" 2>/dev/null || true; }
unit_substate()   { systemctl --user show "$1.service" -p SubState --value 2>/dev/null || true; }
unit_loaded()     { systemctl --user show "$1.service" -p LoadState --value 2>/dev/null || true; }
unit_started()    { systemctl --user show "$1.service" -p ActiveEnterTimestamp --value 2>/dev/null || true; }

print_unit_line() {
  local unit="$1" pretty="$2"
  local active load pid sub started
  active=$(unit_active   "$unit")
  load=$(unit_loaded     "$unit")
  pid=$(unit_pid         "$unit")
  sub=$(unit_substate    "$unit")
  started=$(unit_started "$unit")

  local dot label color
  if [[ "$load" == "not-found" ]]; then
    dot="○"; label="not installed"; color="$C_WARN"
  elif [[ "$active" == "active" ]]; then
    dot="●"; label="running ($sub)"; color="$C_OK"
  elif [[ "$active" == "failed" ]]; then
    dot="●"; label="failed"; color="$C_ERR"
  else
    dot="○"; label="$active"; color="$C_DIM"
  fi

  printf "  %s%s %-22s%s  %s\n" \
    "$color" "$dot" "$pretty" "$C_RESET" "$label"
  if [[ "$active" == "active" && -n "$pid" && "$pid" != "0" ]]; then
    printf "    ${C_DIM}pid${C_RESET} %s   ${C_DIM}since${C_RESET} %s\n" \
      "$pid" "$started"
  elif [[ "$load" == "not-found" ]]; then
    printf "    ${C_DIM}install with:${C_RESET} bash scripts/systemd/install.sh\n"
  fi
}

cmd_status() {
  echo "${C_BOLD}Services${C_RESET}"
  print_unit_line "$WEBUI_UNIT"  "web UI"
  print_unit_line "$DAEMON_UNIT" "orchestrator daemon"

  # Reachability of the web UI's port — independent check, since the
  # unit can be active but the actual TCP listener can be missing
  # (rare but worth surfacing).
  echo
  echo "${C_BOLD}Web UI${C_RESET}"
  if (echo > "/dev/tcp/$WEBUI_HOST/$WEBUI_PORT") >/dev/null 2>&1; then
    printf "  ${C_OK}●${C_RESET} listening at http://%s:%s/\n" "$WEBUI_HOST" "$WEBUI_PORT"
  else
    printf "  ${C_DIM}○${C_RESET} not reachable at %s:%s\n" "$WEBUI_HOST" "$WEBUI_PORT"
  fi

  # Orchestrator lock state — separate from systemd because the lock
  # is the daemon's contract and matters even when systemd thinks the
  # unit is fine.
  local lock="$REPO_ROOT/runs/lab/orchestrator.lock"
  echo
  echo "${C_BOLD}Orchestrator lock${C_RESET}"
  if [[ -f "$lock" ]]; then
    local lock_pid lock_started
    lock_pid=$(grep -o '"pid": *[0-9]*' "$lock" | grep -o '[0-9]*' | head -1)
    lock_started=$(grep -o '"started_at": *"[^"]*"' "$lock" | sed 's/.*"\([^"]*\)"$/\1/')
    if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
      printf "  ${C_OK}●${C_RESET} held by pid %s (since %s)\n" "$lock_pid" "$lock_started"
    else
      printf "  ${C_ERR}●${C_RESET} stale (pid %s — process gone)\n" "${lock_pid:-?}"
      printf "    ${C_DIM}clean up:${C_RESET} rm %s\n" "$lock"
    fi
  else
    printf "  ${C_DIM}○${C_RESET} no lock\n"
  fi

  # Quick links
  echo
  echo "${C_BOLD}Tail logs${C_RESET}"
  printf "  lab svc logs daemon -f   ${C_DIM}# orchestrator${C_RESET}\n"
  printf "  lab svc logs web -f      ${C_DIM}# web UI${C_RESET}\n"
}

# ---------------------------------------------------------------------------
# Start / Stop / Restart
# ---------------------------------------------------------------------------

cmd_action() {
  local action="$1"; shift
  local units; units=$(resolve_unit "${1:-all}")
  for u in $units; do
    local load; load=$(unit_loaded "$u")
    if [[ "$load" == "not-found" ]]; then
      warn "$u.service is not installed; run: bash scripts/systemd/install.sh"
      continue
    fi
    echo "${C_BOLD}$action $u${C_RESET}"
    systemctl --user "$action" "$u.service"
  done
}

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

cmd_logs() {
  local arg1="${1:-all}"; shift || true
  local follow=0
  for a in "$@"; do
    case "$a" in
      -f|--follow) follow=1 ;;
      *) die "unknown logs flag '$a'" ;;
    esac
  done

  local units; units=$(resolve_unit "$arg1")
  local args=(--user --no-pager -n 100)
  [[ "$follow" -eq 1 ]] && args+=(-f)
  for u in $units; do
    args+=(-u "$u.service")
  done
  exec journalctl "${args[@]}"
}

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

cmd_url() { echo "http://$WEBUI_HOST:$WEBUI_PORT/"; }

cmd_install() {
  exec bash "$REPO_ROOT/scripts/systemd/install.sh" "$@"
}

cmd_help() {
  sed -n '2,21p' "$0" | sed 's/^# \?//'
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

cmd="${1:-status}"; shift || true
case "$cmd" in
  status|s)        cmd_status      "$@" ;;
  start)           cmd_action start    "$@" ;;
  stop)            cmd_action stop     "$@" ;;
  restart)         cmd_action restart  "$@" ;;
  logs|log)        cmd_logs        "$@" ;;
  tail)            cmd_logs "${1:-daemon}" -f ;;
  url)             cmd_url         "$@" ;;
  install)         cmd_install     "$@" ;;
  -h|--help|help)  cmd_help ;;
  *) die "unknown command '$cmd' (try: status start stop restart logs tail url install)" ;;
esac
