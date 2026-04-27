"""`uv run lab svc ...` — operator wrapper for systemd-supervised services.

Surface area is deliberately small: the canonical operator workflow is
`lab svc status` first, then `lab svc restart {web,daemon,all}` /
`lab svc logs daemon -f` / `lab svc stop ...`. All mutations route to
``systemctl --user`` so the audit / restart story is identical
whether the operator clicks a button on `/daemon` or runs this from
the shell.

This module is the single Python source-of-truth for those commands;
``scripts/lab-svc.sh`` is a thin bash equivalent retained only for
non-Python contexts (e.g. SSH ForceCommand, CI hooks).

Design notes:

- Strict allow-list for the ``unit`` argument (``web`` / ``daemon``
  / ``all``). A typo can't accidentally target a different systemd
  unit on the host, which would be possible with a generic
  pass-through.
- Status reads go through :mod:`openharness.lab.web.services` so the
  CLI and the web UI's `/daemon` panel agree on schema and edge
  cases (missing units, stale locks, failed runs).
- `logs` and `tail` use ``os.execvp`` to hand the controlling
  terminal directly to ``journalctl``, which keeps Ctrl-C and the
  follow mode well-behaved.
- Stale orchestrator locks are reported as a separate row from the
  systemd state because the lock and the unit can disagree (e.g.
  daemon crashed before signal cleanup landed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from openharness.lab.paths import LAB_RUNS_ROOT
from openharness.lab.web import services as labsvc

console = Console()
err_console = Console(stderr=True)

svc_app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help=("Operate the lab's systemd-supervised services (web UI + orchestrator daemon)."),
)


# ---------------------------------------------------------------------------
# Constants & unit resolution
# ---------------------------------------------------------------------------

# Short aliases the operator can type. The right-hand side must be a
# real key in `labsvc._UNIT_DESC` so we can't accidentally route to
# an unrelated unit on the host.
_UNIT_ALIASES: dict[str, list[labsvc.UnitId]] = {
    "web": ["openharness-lab"],
    "webui": ["openharness-lab"],
    "ui": ["openharness-lab"],
    "daemon": ["openharness-daemon"],
    "d": ["openharness-daemon"],
    "orch": ["openharness-daemon"],
    "all": ["openharness-lab", "openharness-daemon"],
    "both": ["openharness-lab", "openharness-daemon"],
}

_PRETTY_NAME: dict[labsvc.UnitId, str] = {
    "openharness-lab": "web UI",
    "openharness-daemon": "orchestrator daemon",
}

_WEBUI_HOST = os.environ.get("LAB_WEBUI_HOST", "127.0.0.1")
_WEBUI_PORT = int(os.environ.get("LAB_WEBUI_PORT", "8765"))

_LOCK_PATH = LAB_RUNS_ROOT / "orchestrator.lock"


def _resolve_units(arg: str) -> list[labsvc.UnitId]:
    if arg not in _UNIT_ALIASES:
        valid = ", ".join(sorted(set(_UNIT_ALIASES) - {"webui", "ui", "d", "orch", "both"}))
        err_console.print(f"[red]error:[/red] unknown unit '{arg}' (use: {valid})")
        raise typer.Exit(2)
    return _UNIT_ALIASES[arg]


def _systemctl_or_die() -> str:
    bin_ = shutil.which("systemctl")
    if bin_ is None:
        err_console.print(
            "[red]error:[/red] systemctl not found on PATH. "
            "These commands require systemd; install the units with "
            "`bash scripts/systemd/install.sh` first."
        )
        raise typer.Exit(2)
    return bin_


# ---------------------------------------------------------------------------
# `lab svc status`  (also the bare `lab svc`)
# ---------------------------------------------------------------------------


@svc_app.callback()
def _default(ctx: typer.Context) -> None:
    """If no subcommand is given, show status."""
    if ctx.invoked_subcommand is None:
        status()


def _print_unit_line(s: labsvc.UnitStatus) -> None:
    pretty = _PRETTY_NAME.get(s.unit_id, s.unit_id)
    if s.error:
        console.print(f"  [yellow]○[/yellow] {pretty:<22}  unsupervised ({s.error})")
        return
    if s.load_state == "not-found":
        console.print(f"  [yellow]○[/yellow] {pretty:<22}  not installed")
        console.print("    [dim]install with:[/dim] bash scripts/systemd/install.sh")
        return
    if s.is_active and s.active_state == "active":
        sub = s.sub_state or "running"
        console.print(f"  [green]●[/green] {pretty:<22}  running ({sub})")
        if s.main_pid:
            line = f"    [dim]pid[/dim] {s.main_pid}"
            if s.started_at:
                line += f"   [dim]since[/dim] {s.started_at:%Y-%m-%d %H:%M:%S %Z}"
            console.print(line)
        return
    if s.active_state == "failed":
        console.print(f"  [red]●[/red] {pretty:<22}  failed")
        console.print(
            "    [dim]inspect:[/dim] lab svc logs "
            f"{'web' if s.unit_id == 'openharness-lab' else 'daemon'}"
        )
        return
    state = s.active_state or "unknown"
    console.print(f"  [dim]○[/dim] {pretty:<22}  {state}")


def _port_listening(host: str, port: int) -> bool:
    """Cheap connect-and-close to test whether someone is bound."""
    import socket as _sock

    s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _read_lock() -> tuple[Optional[int], Optional[str]]:
    """Return (pid, started_at) from the orchestrator lock, or (None, None)."""
    if not _LOCK_PATH.exists():
        return None, None
    try:
        data = json.loads(_LOCK_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None
    pid = data.get("pid")
    started = data.get("started_at")
    return (
        int(pid) if isinstance(pid, int) else None,
        str(started) if isinstance(started, str) else None,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


@svc_app.command("status")
def status() -> None:
    """Show health of every supervised service in one screen."""
    if not labsvc.available():
        err_console.print(
            "[yellow]warn:[/yellow] systemctl not on PATH — showing minimal info only."
        )

    console.print("[bold]Services[/bold]")
    for uid in ("openharness-lab", "openharness-daemon"):
        _print_unit_line(labsvc.status(uid))

    console.print()
    console.print("[bold]Web UI[/bold]")
    if _port_listening(_WEBUI_HOST, _WEBUI_PORT):
        console.print(f"  [green]●[/green] listening at http://{_WEBUI_HOST}:{_WEBUI_PORT}/")
    else:
        console.print(f"  [dim]○[/dim] not reachable at {_WEBUI_HOST}:{_WEBUI_PORT}")

    console.print()
    console.print("[bold]Orchestrator lock[/bold]")
    pid, started = _read_lock()
    if pid is None:
        console.print("  [dim]○[/dim] no lock")
    elif _pid_alive(pid):
        console.print(
            f"  [green]●[/green] held by pid {pid}" + (f" (since {started})" if started else "")
        )
    else:
        console.print(f"  [red]●[/red] stale (pid {pid} — process gone)")
        console.print(f"    [dim]clean up:[/dim] rm {_LOCK_PATH}")

    console.print()
    console.print("[bold]Tail logs[/bold]")
    console.print("  [cyan]lab svc logs daemon -f[/cyan]   [dim]# orchestrator[/dim]")
    console.print("  [cyan]lab svc logs web -f[/cyan]      [dim]# web UI[/dim]")


# ---------------------------------------------------------------------------
# Lifecycle: start / stop / restart
# ---------------------------------------------------------------------------


def _do_action(action: str, unit_arg: str) -> None:
    bin_ = _systemctl_or_die()
    units = _resolve_units(unit_arg)
    failures = 0
    for u in units:
        s = labsvc.status(u)
        if s.load_state == "not-found":
            err_console.print(
                f"[yellow]warn:[/yellow] {u}.service is not installed; "
                "run: bash scripts/systemd/install.sh"
            )
            failures += 1
            continue
        console.print(f"[bold]{action}[/bold] {u}")
        rc = subprocess.call([bin_, "--user", action, f"{u}.service"])
        if rc != 0:
            err_console.print(f"[red]error:[/red] systemctl {action} {u}.service exited {rc}")
            failures += 1
    if failures:
        raise typer.Exit(1)


@svc_app.command("start")
def start(
    unit: str = typer.Argument("all", help="web | daemon | all (default)"),
) -> None:
    """Start one supervised service, or all of them."""
    _do_action("start", unit)


@svc_app.command("stop")
def stop(
    unit: str = typer.Argument("all", help="web | daemon | all (default)"),
) -> None:
    """Stop one supervised service, or all of them."""
    _do_action("stop", unit)


@svc_app.command("restart")
def restart(
    unit: str = typer.Argument("all", help="web | daemon | all (default)"),
) -> None:
    """Restart one supervised service, or all of them.

    Use ``lab svc restart web`` after editing web UI code, or
    ``lab svc restart daemon`` to pick up runner changes. The daemon
    catches SIGTERM and releases the orchestrator lock cleanly.
    """
    _do_action("restart", unit)


# ---------------------------------------------------------------------------
# Logs (journalctl pass-through)
# ---------------------------------------------------------------------------


@svc_app.command("logs")
def logs(
    unit: str = typer.Argument("all", help="web | daemon | all (default)"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow live."),
    lines: int = typer.Option(100, "-n", "--lines", min=0, help="Tail length."),
) -> None:
    """Show recent journal entries for a supervised unit.

    Hands control directly to ``journalctl`` via execvp so Ctrl-C
    and follow mode behave exactly as if you ran journalctl
    yourself.
    """
    bin_ = shutil.which("journalctl")
    if bin_ is None:
        err_console.print("[red]error:[/red] journalctl not found on PATH.")
        raise typer.Exit(2)
    units = _resolve_units(unit)
    args: list[str] = [bin_, "--user", "--no-pager", "-n", str(lines)]
    if follow:
        args.append("-f")
    for u in units:
        args.extend(["-u", f"{u}.service"])
    os.execvp(bin_, args)  # never returns


@svc_app.command("tail")
def tail(
    unit: str = typer.Argument("daemon", help="web | daemon | all (default daemon)"),
) -> None:
    """Alias for ``logs <unit> -f``. Default unit is daemon."""
    logs(unit=unit, follow=True, lines=100)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


@svc_app.command("url")
def url() -> None:
    """Print the canonical web UI URL on this host."""
    print(f"http://{_WEBUI_HOST}:{_WEBUI_PORT}/")


@svc_app.command("install")
def install(
    args: list[str] = typer.Argument(
        None,
        help="Forwarded to scripts/systemd/install.sh (e.g. --no-start, --uninstall).",
    ),
) -> None:
    """Run scripts/systemd/install.sh to install / refresh the units.

    All extra positional args are forwarded verbatim, so this is a
    1-1 wrapper around the installer; we just save the operator
    from typing the path.
    """
    repo_root = Path(__file__).resolve().parents[3]
    installer = repo_root / "scripts" / "systemd" / "install.sh"
    if not installer.exists():
        err_console.print(f"[red]error:[/red] {installer} not found")
        raise typer.Exit(2)
    cmd = ["bash", str(installer), *(args or [])]
    raise typer.Exit(subprocess.call(cmd))
