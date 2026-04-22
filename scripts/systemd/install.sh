#!/usr/bin/env bash
# Install / refresh the systemd --user units for the lab.
#
# Usage:
#   bash scripts/systemd/install.sh                # install both units
#   bash scripts/systemd/install.sh --no-start     # write files only
#   bash scripts/systemd/install.sh --uninstall    # remove + disable
#
# Idempotent: safe to re-run. Always reloads the user systemd manager.
# Does NOT enable lingering (so units stop at logout); enable that
# manually with `loginctl enable-linger $USER` if you want the lab to
# survive a reboot when nobody is logged in.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_UNITS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(openharness-lab openharness-daemon)

action="install"
auto_start=1
for arg in "$@"; do
  case "$arg" in
    --no-start)  auto_start=0 ;;
    --uninstall) action="uninstall" ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$USER_UNITS_DIR"

if [[ "$action" == "uninstall" ]]; then
  for unit in "${UNITS[@]}"; do
    systemctl --user disable --now "${unit}.service" 2>/dev/null || true
    rm -f "${USER_UNITS_DIR}/${unit}.service"
    echo "removed: ${USER_UNITS_DIR}/${unit}.service"
  done
  systemctl --user daemon-reload
  echo "done."
  exit 0
fi

# install path
for unit in "${UNITS[@]}"; do
  src="${SCRIPT_DIR}/${unit}.service"
  dst="${USER_UNITS_DIR}/${unit}.service"
  if [[ ! -f "$src" ]]; then
    echo "missing source unit: $src" >&2
    exit 1
  fi
  # Diff before clobber so the operator sees what changed.
  if [[ -f "$dst" ]]; then
    if ! diff -q "$src" "$dst" >/dev/null; then
      echo "updating ${dst}:"
      diff -u "$dst" "$src" || true
    fi
  else
    echo "installing ${dst}"
  fi
  install -m 0644 "$src" "$dst"
done

systemctl --user daemon-reload

for unit in "${UNITS[@]}"; do
  systemctl --user enable "${unit}.service"
  if [[ "$auto_start" -eq 1 ]]; then
    # `restart` (not `start`) so an already-running unit picks up
    # the new ExecStart / env from the file we just wrote.
    systemctl --user restart "${unit}.service"
  fi
  systemctl --user --no-pager --full status "${unit}.service" || true
done

echo
echo "done. tail logs with:"
for unit in "${UNITS[@]}"; do
  echo "  journalctl --user -u ${unit} -f"
done
