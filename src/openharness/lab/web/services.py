"""Supervisor abstraction over ``systemctl --user`` for the lab.

The lab has two long-running services worth supervising centrally:

- ``openharness-lab.service``   — the FastAPI web UI (this process,
  in production).
- ``openharness-daemon.service`` — the orchestrator that walks the
  roadmap and spawns experiment runs.

Both are user units (``systemctl --user``) so they live in the
operator's session and don't need root. This module gives the rest
of the codebase one place to:

- name the units (``UnitId``);
- inspect their state without parsing systemctl prose
  (:func:`status` returns a typed :class:`UnitStatus`);
- enumerate everything we know about (:func:`all_status`) so a single
  web partial can render the whole supervisor surface;
- locate the unit file on disk (:func:`unit_file_path`) so the
  installer can reason about whether we need to write/update it.

The module is **read-only**: actually starting / stopping / restarting
units happens through the existing whitelist in
``openharness.lab.web.commands``. That keeps the audit-log invariant
("every mutation goes through ``run_command``") intact and means we
don't have a second privilege path.

Failure modes are deliberately soft. ``systemctl`` not being on
``$PATH`` (test env, dev container without systemd) is treated as
"unsupervised" rather than an exception, so the rest of the web UI
keeps working. The ``available()`` predicate lets templates render
a clean "supervisor not present" panel instead.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


__all__ = [
    "UnitId",
    "UnitStatus",
    "available",
    "status",
    "all_status",
    "unit_file_path",
    "UNITS",
]


UnitId = Literal["openharness-lab", "openharness-daemon"]

# Stable display order — webui first because it's how operators get
# *to* the dashboard at all; daemon second because it's the workload.
UNITS: list[UnitId] = ["openharness-lab", "openharness-daemon"]

_UNIT_DESC: dict[UnitId, str] = {
    "openharness-lab":    "FastAPI web UI (this process)",
    "openharness-daemon": "Orchestrator daemon (walks the roadmap)",
}


@dataclass(eq=False, slots=True)
class UnitStatus:
    """One unit's posture as `systemctl --user` reports it.

    All string fields are normalised lowercase systemd values; ``None``
    means we couldn't get the field (unit missing, systemctl absent,
    transient parse failure). The ``main_pid`` is the supervised
    process; ``started_at`` is when it last entered the active state
    (so a Restart=always unit reports the latest restart, not the
    very first start, which matches operator expectations).
    """

    unit_id: UnitId
    description: str
    # systemd's "LoadState" — one of "loaded", "not-found", "error", …
    load_state: str | None
    # "ActiveState" — one of "active", "inactive", "failed",
    # "activating", "deactivating", "reloading".
    active_state: str | None
    # "SubState" — finer-grained per-unit-type ("running", "exited", …).
    sub_state: str | None
    # Whether the unit is currently the foreground active process.
    is_active: bool
    # Whether the unit is enabled (=will start on session login).
    is_enabled: bool | None
    # Whether the unit file exists on disk at all.
    is_installed: bool
    main_pid: int | None
    started_at: datetime | None
    # systemctl exit code for `is-active` — 0 = active, 3 = inactive,
    # 1 / 4 = unknown / not loaded. Useful for tests.
    is_active_rc: int | None = None
    # If we couldn't reach systemctl at all.
    error: str | None = None
    # Computed UI helpers.
    can_start: bool = field(init=False)
    can_stop: bool = field(init=False)
    can_restart: bool = field(init=False)

    def __post_init__(self) -> None:
        # Whatever systemctl told us, only offer transitions that
        # make sense. A unit that isn't installed can't be started
        # from the UI (the installer is a separate flow).
        installed = self.is_installed and self.load_state in {"loaded", None}
        running = self.is_active and self.active_state == "active"
        self.can_start   = installed and not running
        self.can_stop    = installed and running
        self.can_restart = installed  # restart is idempotent regardless


# ---------------------------------------------------------------------------
# systemctl wrappers
# ---------------------------------------------------------------------------


def _systemctl() -> str | None:
    """Path to ``systemctl`` on the host, or ``None`` if absent."""
    return shutil.which("systemctl")


def available() -> bool:
    """Whether the host has ``systemctl --user`` we can talk to.

    Templates use this to decide between "show the supervisor panel"
    and "show a stub explaining no supervisor is configured" (e.g.
    in CI / dev container). We don't try to detect whether the user
    bus is actually reachable — that surfaces naturally as
    :class:`UnitStatus` with ``error != None`` for each unit.
    """
    return _systemctl() is not None


def _systemctl_show(unit: str) -> dict[str, str]:
    """Run ``systemctl --user show`` and parse into a dict.

    ``show`` outputs ``Key=Value`` per line, which is trivial to
    split on the *first* ``=`` (values may contain ``=``).
    """
    bin_ = _systemctl()
    if bin_ is None:
        return {}
    try:
        completed = subprocess.run(
            [bin_, "--user", "show", "--no-pager",
             "--property=LoadState,ActiveState,SubState,UnitFileState,"
             "MainPID,ActiveEnterTimestamp,Description,FragmentPath",
             unit + ".service"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    out: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _systemctl_is_active(unit: str) -> tuple[bool, int | None]:
    bin_ = _systemctl()
    if bin_ is None:
        return False, None
    try:
        cp = subprocess.run(
            [bin_, "--user", "is-active", unit + ".service"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, None
    # `is-active` returns 0 if active, 3 if inactive, etc. Only
    # treat exit 0 as positive — `activating` would be reported by
    # ActiveState anyway, picked up by the show() call.
    return cp.returncode == 0 and cp.stdout.strip() == "active", cp.returncode


def _systemctl_is_enabled(unit: str) -> bool | None:
    bin_ = _systemctl()
    if bin_ is None:
        return None
    try:
        cp = subprocess.run(
            [bin_, "--user", "is-enabled", unit + ".service"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    state = cp.stdout.strip()
    if state in {"enabled", "alias", "static", "linked"}:
        return True
    if state in {"disabled", "masked", "linked-runtime", "indirect"}:
        return False
    # `unknown` / anything else — caller treats as None.
    return None


def _parse_systemd_ts(raw: str) -> datetime | None:
    """Parse the ``ActiveEnterTimestamp`` format.

    systemd emits e.g. ``Tue 2026-04-21 21:04:28 UTC`` — timezone
    name varies by host locale. We try a few common shapes; if none
    match, return None (UI just shows "—").
    """
    if not raw or raw == "n/a":
        return None
    for fmt in (
        "%a %Y-%m-%d %H:%M:%S %Z",
        "%a %Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            # If parsing succeeded without tz info, assume UTC since
            # systemd-on-server is almost always UTC.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def status(unit: UnitId) -> UnitStatus:
    """Return a typed status snapshot for ``unit``.

    Never raises. Network / parse / missing-binary failures show up
    in the returned object's ``error`` / ``load_state`` / ``main_pid``
    fields rather than as exceptions.
    """
    bin_ = _systemctl()
    if bin_ is None:
        return UnitStatus(
            unit_id=unit,
            description=_UNIT_DESC.get(unit, unit),
            load_state=None,
            active_state=None,
            sub_state=None,
            is_active=False,
            is_enabled=None,
            is_installed=False,
            main_pid=None,
            started_at=None,
            error="systemctl not available on PATH",
        )

    show = _systemctl_show(unit)
    is_active, rc = _systemctl_is_active(unit)
    is_enabled = _systemctl_is_enabled(unit)
    load_state = show.get("LoadState") or None
    active_state = show.get("ActiveState") or None
    sub_state = show.get("SubState") or None
    fragment = show.get("FragmentPath") or ""
    pid_raw = show.get("MainPID") or "0"
    try:
        main_pid: int | None = int(pid_raw) or None
    except ValueError:
        main_pid = None
    started_at = _parse_systemd_ts(show.get("ActiveEnterTimestamp", ""))
    is_installed = bool(fragment) and load_state == "loaded"

    return UnitStatus(
        unit_id=unit,
        description=_UNIT_DESC.get(unit, unit),
        load_state=load_state,
        active_state=active_state,
        sub_state=sub_state,
        is_active=is_active,
        is_enabled=is_enabled,
        is_installed=is_installed,
        main_pid=main_pid,
        started_at=started_at,
        is_active_rc=rc,
    )


def all_status() -> list[UnitStatus]:
    """Snapshot every known unit. Order matches :data:`UNITS`."""
    return [status(u) for u in UNITS]


# ---------------------------------------------------------------------------
# Unit file installation (read-only inspection — actual writes happen
# via the install script, see scripts/install-systemd-units.sh)
# ---------------------------------------------------------------------------


def unit_file_path(unit: UnitId) -> str:
    """Canonical install path for the user-level unit file.

    We don't honour ``$XDG_CONFIG_HOME`` here because the rest of
    the deployment runbook hard-codes ``~/.config/systemd/user``;
    diverging would just confuse operators. If you need a different
    path, override at the install script level.
    """
    from pathlib import Path

    return str(Path("~/.config/systemd/user").expanduser() / f"{unit}.service")
