"""Whitelisted ``uv run lab`` invocations from the web UI.

The web UI is *not* a privileged writer. Every state mutation must
flow through the same Typer CLI that humans, skills, and the daemon
use, so that the audit story is identical regardless of who invoked
it. This module enforces that contract:

- A static :data:`COMMANDS` registry maps a short ``cmd_id`` to an
  argv template + a list of :class:`ParamSpec` validators.
- :func:`run_command` validates user-supplied params against each
  spec's regex, builds the final argv, shells out via ``uv run lab``
  in the repo root, and appends a JSONL audit row to
  ``runs/lab/web_commands.jsonl``.
- Anything not in the registry is rejected outright (``KeyError``
  becomes :class:`CommandError`).

Returned :class:`CommandResult` carries enough structure that the
caller (the FastAPI route) can render a nice HTMX fragment without
re-running the command.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openharness.lab.paths import LAB_RUNS_ROOT, REPO_ROOT, ensure_lab_runs_dir

__all__ = [
    "CommandError",
    "CommandResult",
    "CommandSpec",
    "ParamSpec",
    "COMMANDS",
    "run_command",
    "trigger_events",
    "audit_tail",
    "audit_log_path",
]


# ---------------------------------------------------------------------------
# Specs + result types
# ---------------------------------------------------------------------------


# Slugs/idea-ids look like ``foo-bar-2024-04-17`` or ``arch.tools.shell``.
# Keep it loose enough to match anything the lab actually emits but tight
# enough to refuse shell metacharacters.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:\-/]{0,127}$")
_IDEA_TARGETS = re.compile(r"^(?:proposed|trying|graduated|rejected)$", re.IGNORECASE)
_ROADMAP_SECTIONS = re.compile(r"^(?:up-next|suggested|done)$")


@dataclass(eq=False, slots=True)
class ParamSpec:
    name: str
    pattern: re.Pattern[str]
    label: str
    required: bool = True
    default: str | None = None
    help_text: str | None = None
    placeholder: str | None = None


@dataclass(eq=False, slots=True)
class CommandSpec:
    cmd_id: str
    label: str
    description: str
    # Argv tokens after ``uv run lab`` — placeholders use ``{name}``.
    argv_template: list[str]
    params: list[ParamSpec]
    confirm_text: str | None = None
    danger: bool = False  # show in red, always require explicit confirm
    # Custom DOM events to dispatch in the browser after a successful run.
    # Listening containers re-fetch their partial endpoint so stale
    # rows disappear without a full page reload. Always include the
    # generic ``lab-cmd-success`` so cross-cutting widgets can react.
    events: list[str] = field(default_factory=list)


@dataclass(eq=False, slots=True)
class CommandResult:
    cmd_id: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    started_at: datetime
    duration_ms: int
    actor: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def display_argv(self) -> str:
        return shlex.join(self.argv)


class CommandError(RuntimeError):
    """Raised on validation failure or a missing whitelist entry.

    Distinct from a non-zero exit; the latter is returned as a
    :class:`CommandResult` with ``exit_code != 0`` so the caller can
    still render stdout/stderr.
    """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


COMMANDS: dict[str, CommandSpec] = {
    "graduate-confirm": CommandSpec(
        cmd_id="graduate-confirm",
        label="Confirm trunk swap",
        description=(
            "Promote the staged Graduate diff to trunk: copies the new "
            "branch YAML into trunk.yaml and writes an audit row."
        ),
        argv_template=["graduate", "confirm", "{slug}", "--applied-by", "{applied_by}"],
        params=[
            ParamSpec(
                name="slug",
                pattern=_SAFE_TOKEN,
                label="Experiment slug",
                placeholder="tb2-runtime-shell-shim",
            ),
            ParamSpec(
                name="applied_by",
                pattern=_SAFE_ACTOR,
                label="Applied by",
                default="human:webui",
                help_text="Who is making the call (recorded in the audit log).",
            ),
        ],
        confirm_text=(
            "This will swap the active trunk to the new branch. Continue?"
        ),
        danger=True,
        events=["lab-pending-changed", "lab-tree-changed", "lab-roadmap-changed"],
    ),
    "roadmap-promote": CommandSpec(
        cmd_id="roadmap-promote",
        label="Promote to roadmap",
        description=(
            "Move a `### Suggested > #### <slug>` entry into the main "
            "`## Up next` queue."
        ),
        argv_template=["roadmap", "promote", "{slug}"],
        params=[
            ParamSpec(
                name="slug",
                pattern=_SAFE_TOKEN,
                label="Suggested slug",
            ),
        ],
        confirm_text=None,
        events=["lab-pending-changed", "lab-roadmap-changed"],
    ),
    "roadmap-demote": CommandSpec(
        cmd_id="roadmap-demote",
        label="Demote from roadmap",
        description=(
            "Move `## Up next > ### <slug>` back into "
            "`### Suggested > #### <slug>`. Inverse of promote."
        ),
        argv_template=["roadmap", "demote", "{slug}"],
        params=[
            ParamSpec(name="slug", pattern=_SAFE_TOKEN, label="Up-next slug"),
        ],
        confirm_text=None,
        events=["lab-pending-changed", "lab-roadmap-changed"],
    ),
    "roadmap-remove": CommandSpec(
        cmd_id="roadmap-remove",
        label="Remove roadmap entry",
        description=(
            "Delete a roadmap entry by slug. Default scans Up next, "
            "Suggested, and Done; pass --section to constrain."
        ),
        argv_template=["roadmap", "remove", "{slug}", "--section", "{section}"],
        params=[
            ParamSpec(name="slug", pattern=_SAFE_TOKEN, label="Slug"),
            ParamSpec(
                name="section",
                pattern=_ROADMAP_SECTIONS,
                label="Section",
                placeholder="up-next | suggested | done",
            ),
        ],
        confirm_text="Permanently remove this roadmap entry?",
        danger=True,
        events=["lab-pending-changed", "lab-roadmap-changed"],
    ),
    "daemon-start": CommandSpec(
        cmd_id="daemon-start",
        label="Start daemon",
        description=(
            "Spawn the orchestrator daemon in the background (tmux "
            "session if available, else detached nohup). Returns "
            "immediately; the daemon then walks the roadmap on its own."
        ),
        argv_template=["daemon", "start", "--background"],
        params=[],
        confirm_text=None,
        events=["lab-daemon-changed", "lab-pending-changed"],
    ),
    "daemon-stop": CommandSpec(
        cmd_id="daemon-stop",
        label="Stop daemon",
        description=(
            "SIGTERM the recorded orchestrator pid. In-flight "
            "experiments keep running; new roadmap entries are not "
            "picked up until the daemon is restarted."
        ),
        argv_template=["daemon", "stop"],
        params=[],
        confirm_text=(
            "Stop the running orchestrator? In-flight experiments will "
            "continue but no new roadmap entries will be picked up."
        ),
        danger=True,
        events=["lab-daemon-changed", "lab-pending-changed"],
    ),
    "tree-apply": CommandSpec(
        cmd_id="tree-apply",
        label="Apply tree verdict",
        description=(
            "Recompute the TreeDiff for an experiment slug and apply it. "
            "AddBranch / Reject / NoOp land immediately; Graduate is "
            "STAGED for `graduate confirm`."
        ),
        argv_template=["tree", "apply", "{slug}", "--applied-by", "{applied_by}"],
        params=[
            ParamSpec(
                name="slug",
                pattern=_SAFE_TOKEN,
                label="Experiment slug",
                placeholder="tb2-runtime-shell-shim",
            ),
            ParamSpec(
                name="applied_by",
                pattern=_SAFE_ACTOR,
                label="Applied by",
                default="human:webui",
                help_text="Recorded in the audit trail.",
            ),
        ],
        confirm_text=(
            "Apply this verdict to the lab? AddBranch / Reject / NoOp "
            "edit configs.md immediately; Graduate stages a trunk swap "
            "that you'll then need to confirm separately."
        ),
        events=[
            "lab-pending-changed",
            "lab-tree-changed",
            "lab-roadmap-changed",
        ],
    ),
    "idea-move": CommandSpec(
        cmd_id="idea-move",
        label="Move idea",
        description=(
            "Move an entry between top-level sections of `lab/ideas.md` "
            "(Proposed / Trying / Graduated / Rejected)."
        ),
        argv_template=["idea", "move", "{idea_id}", "{target}"],
        params=[
            ParamSpec(name="idea_id", pattern=_SAFE_TOKEN, label="Idea id"),
            ParamSpec(
                name="target",
                pattern=_IDEA_TARGETS,
                label="Target section",
                placeholder="proposed | trying | graduated | rejected",
            ),
        ],
        confirm_text=None,
        events=["lab-pending-changed", "lab-ideas-changed"],
    ),
}


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def trigger_events(cmd_id: str) -> list[str]:
    """Return the list of DOM events to dispatch after a successful run.

    Always includes ``lab-cmd-success`` so cross-cutting widgets (e.g.
    a global activity counter) can react without enumerating every
    cmd_id. Returns an empty list for unknown ``cmd_id`` so callers can
    no-op gracefully.
    """
    spec = COMMANDS.get(cmd_id)
    if spec is None:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ev in [*spec.events, "lab-cmd-success"]:
        if ev not in seen:
            seen.add(ev)
            out.append(ev)
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


_AUDIT_LOG_PATH = LAB_RUNS_ROOT / "web_commands.jsonl"
_DEFAULT_TIMEOUT_S = 120


def _validate_params(spec: CommandSpec, raw: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in spec.params:
        if p.name in raw and raw[p.name] != "":
            value = raw[p.name].strip()
        elif p.default is not None:
            value = p.default
        elif p.required:
            raise CommandError(f"missing required param {p.name!r}")
        else:
            continue
        if not p.pattern.fullmatch(value):
            raise CommandError(
                f"param {p.name!r} value {value!r} does not match "
                f"required pattern {p.pattern.pattern!r}"
            )
        out[p.name] = value
    # Reject extras outright — defence-in-depth against accidental shell
    # injection through unknown form fields.
    extras = {k for k in raw if k not in {p.name for p in spec.params}} - {"cmd_id"}
    if extras:
        raise CommandError(f"unexpected param(s): {sorted(extras)}")
    return out


def _build_argv(spec: CommandSpec, params: dict[str, str]) -> list[str]:
    argv: list[str] = []
    for token in spec.argv_template:
        if token.startswith("{") and token.endswith("}"):
            key = token[1:-1]
            if key not in params:
                raise CommandError(f"argv template references missing param {key!r}")
            argv.append(params[key])
        else:
            argv.append(token)
    return argv


def _record(result: CommandResult) -> None:
    ensure_lab_runs_dir()
    payload = asdict(result)
    payload["started_at"] = result.started_at.isoformat()
    with _AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_command(
    cmd_id: str,
    raw_params: dict[str, str],
    *,
    actor: str = "human:webui",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> CommandResult:
    """Validate + execute a whitelisted CLI invocation.

    Returns a :class:`CommandResult` even when the command exited
    non-zero. Raises :class:`CommandError` only for validation
    failures (bad cmd_id, regex miss, missing param, unknown extras).
    """
    spec = COMMANDS.get(cmd_id)
    if spec is None:
        raise CommandError(f"unknown cmd_id {cmd_id!r}")

    if not _SAFE_ACTOR.fullmatch(actor):
        raise CommandError(f"actor {actor!r} contains forbidden characters")

    params = _validate_params(spec, raw_params)
    cli_args = _build_argv(spec, params)
    argv = ["uv", "run", "lab", *cli_args]

    started = datetime.now(timezone.utc)
    env = os.environ.copy()
    # Some downstream code reads LAB_USER for attribution; surface the
    # actor so anything we don't pass via flags still picks it up.
    env.setdefault("LAB_USER", actor)

    try:
        completed = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_s,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as e:
        exit_code = 124  # GNU timeout's convention
        stdout = e.stdout.decode("utf-8", "replace") if e.stdout else ""
        stderr = (
            (e.stderr.decode("utf-8", "replace") if e.stderr else "")
            + f"\n[web] command timed out after {timeout_s}s"
        )
    except FileNotFoundError as e:
        # `uv` not on PATH inside the server env. Surface, don't crash.
        exit_code = 127
        stdout = ""
        stderr = f"[web] failed to spawn command: {e}"
    finally:
        ended = datetime.now(timezone.utc)

    duration_ms = int((ended - started).total_seconds() * 1000)
    result = CommandResult(
        cmd_id=cmd_id,
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started,
        duration_ms=duration_ms,
        actor=actor,
        params=params,
    )
    _record(result)
    return result


def audit_tail(n: int = 50) -> list[dict[str, object]]:
    """Read the last ``n`` audit rows for display in the UI."""
    if not _AUDIT_LOG_PATH.is_file():
        return []
    with _AUDIT_LOG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    out: list[dict[str, object]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def audit_log_path() -> Path:
    return _AUDIT_LOG_PATH
