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
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openharness.lab.paths import LAB_RUNS_ROOT, REPO_ROOT, ensure_lab_runs_dir
from openharness.lab.web import services as labsvc

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

# Free text fields (motivation, hypothesis, plan, evidence, …). Allow any
# printable character plus tab/newline/CR; reject other ASCII control
# characters and a leading hyphen (so the value can't be misread as a
# CLI flag by Click downstream). Length cap is generous but bounded so
# nobody can paste 10 MB of binary into the CLI.
_SAFE_TEXT = re.compile(
    r"(?!-)[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]{1,512}"
)
_SAFE_LONG_TEXT = re.compile(
    r"(?!-)[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]{1,4096}"
)
# Constrained vocabularies enforced by the CLI itself; we duplicate them
# here so the form gets a fast 400 instead of waiting for ``uv run`` to
# spin up and Typer to reject.
_IDEA_THEME = re.compile(r"^(?:Architecture|Runtime|Tools|Memory)$")
_COMPONENT_KIND = re.compile(r"^(?:Architecture|Runtime|Tools|Prompt|Model)$")
_COMPONENT_STATUS = re.compile(
    r"^(?:proposed|experimental|branch|validated|rejected|superseded)$"
)
# Comma-separated list of agent ids; each entry is a SAFE_TOKEN.
_USED_BY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}(?:,[A-Za-z0-9][A-Za-z0-9_.\-]{0,127})*$"
)
# Whitelisted systemd unit ids the web UI may operate on. Hard-coded
# (not user-supplied) so the action stays "restart THIS unit"; never
# "restart whatever the operator typed". Matches keys in
# :data:`openharness.lab.web.services.UNITS`.
_UNIT_ID = re.compile(r"^(?:openharness-lab|openharness-daemon)$")
# Numeric PID — bounded so we can't pass an absurd value.
_PID_RE = re.compile(r"^[0-9]{1,7}$")
# Daemon operating modes, must match :data:`openharness.lab.daemon_state.DaemonMode`.
_DAEMON_MODE = re.compile(r"^(?:paused|manual|autonomous)$")


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
    # Argv tokens after ``uv run lab``. Three shapes are supported:
    #   - literal token, e.g. ``"roadmap"``
    #   - placeholder, e.g. ``"{slug}"`` → substituted from validated params
    #   - optional group, e.g. ``["--idea", "{idea}"]`` → included only if
    #     EVERY ``{name}`` in the group resolved to a value. The whole
    #     group is dropped silently otherwise. This is how we model
    #     optional CLI flags (``--cost X``, ``--depends-on Y``) without
    #     having to special-case the empty string in argv assembly.
    argv_template: list[str | list[str]]
    params: list[ParamSpec]
    confirm_text: str | None = None
    danger: bool = False  # show in red, always require explicit confirm
    # Custom DOM events to dispatch in the browser after a successful run.
    # Listening containers re-fetch their partial endpoint so stale
    # rows disappear without a full page reload. Always include the
    # generic ``lab-cmd-success`` so cross-cutting widgets can react.
    events: list[str] = field(default_factory=list)
    # Argv prefix that ``argv_template`` is appended to. Defaults to the
    # ``uv run lab`` CLI so existing entries don't change shape; opt out
    # by setting to e.g. ``["systemctl", "--user"]`` for service ops or
    # to ``[]`` if argv_template already starts with the binary path.
    # Resolution: if the first element is one of ``("systemctl", "kill",
    # "uv")``, ``run_command`` will look it up via ``shutil.which`` so
    # an absent binary fails fast with exit 127 instead of leaking a
    # FileNotFoundError. Don't put user input in this field.
    argv_prefix: list[str] = field(
        default_factory=lambda: ["uv", "run", "lab"],
    )
    # Hook called with validated params before subprocess spawn. Raise
    # :class:`CommandError` to abort. Used for safety checks that don't
    # fit a regex (e.g. "the supplied PID must be a descendant of the
    # orchestrator daemon"). Returns ``None`` on success.
    precheck: Callable[[dict[str, str]], None] | None = None


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
            "Start the ``openharness-daemon`` systemd --user unit. "
            "systemd owns the process tree, captures stdout/stderr to "
            "journald, and restarts on crash. Replaces the older "
            "tmux/nohup backgrounding."
        ),
        argv_prefix=["systemctl", "--user"],
        argv_template=["start", "openharness-daemon.service"],
        params=[],
        confirm_text=None,
        events=["lab-daemon-changed", "lab-pending-changed", "lab-services-changed"],
    ),
    "daemon-stop": CommandSpec(
        cmd_id="daemon-stop",
        label="Stop daemon",
        description=(
            "Stop the ``openharness-daemon`` unit cleanly (SIGTERM, "
            "60 s grace, then SIGKILL). In-flight experiments continue "
            "running in their own processes; new roadmap entries are "
            "not picked up until you restart."
        ),
        argv_prefix=["systemctl", "--user"],
        argv_template=["stop", "openharness-daemon.service"],
        params=[],
        confirm_text=(
            "Stop the running orchestrator? In-flight experiments will "
            "continue but no new roadmap entries will be picked up."
        ),
        danger=True,
        events=["lab-daemon-changed", "lab-pending-changed", "lab-services-changed"],
    ),
    "daemon-restart": CommandSpec(
        cmd_id="daemon-restart",
        label="Restart daemon",
        description=(
            "Restart the ``openharness-daemon`` unit. Idempotent: "
            "starts the unit if it wasn't running. Useful when a "
            "config or skill change needs to be picked up cleanly."
        ),
        argv_prefix=["systemctl", "--user"],
        argv_template=["restart", "openharness-daemon.service"],
        params=[],
        confirm_text=(
            "Restart the orchestrator? Current tick is interrupted; "
            "in-flight experiments keep running in their own processes."
        ),
        events=["lab-daemon-changed", "lab-pending-changed", "lab-services-changed"],
    ),
    "service-restart": CommandSpec(
        cmd_id="service-restart",
        label="Restart unit",
        description=(
            "Restart any whitelisted systemd --user unit "
            "(``openharness-lab`` for the web UI itself, "
            "``openharness-daemon`` for the orchestrator). Restarting "
            "the web UI tears down the current HTMX session — the "
            "browser will see a brief network error before reconnecting."
        ),
        argv_prefix=["systemctl", "--user"],
        argv_template=["restart", "{unit}.service"],
        params=[
            ParamSpec(
                name="unit",
                pattern=_UNIT_ID,
                label="Unit",
                placeholder="openharness-lab | openharness-daemon",
            ),
        ],
        confirm_text=(
            "Restart this unit? If it's the web UI itself, the page "
            "will lose connection for a few seconds before HTMX "
            "reconnects."
        ),
        danger=True,
        events=["lab-daemon-changed", "lab-services-changed"],
    ),
    "kill-process": CommandSpec(
        cmd_id="kill-process",
        label="Kill process",
        description=(
            "SIGTERM a specific PID under the orchestrator's process "
            "tree. Refuses any PID that isn't a descendant of the "
            "running daemon, so we can clean up wedged experiment "
            "subprocesses without ever touching unrelated VM processes."
        ),
        argv_prefix=["kill"],
        argv_template=["-TERM", "{pid}"],
        params=[
            ParamSpec(
                name="pid",
                pattern=_PID_RE,
                label="PID",
                help_text="Must be a descendant of the orchestrator daemon.",
            ),
        ],
        confirm_text=(
            "SIGTERM this process? Lab subprocesses usually catch "
            "SIGTERM and shut down cleanly; if it ignores the signal, "
            "use the OS for SIGKILL."
        ),
        danger=True,
        events=["lab-processes-changed"],
        # Filled in below — defined as a closure so it can import
        # lazily without a top-level psutil dependency at module load.
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
    "idea-append": CommandSpec(
        cmd_id="idea-append",
        label="Propose new idea",
        description=(
            "Append a new entry to `## Proposed > <Theme>` in "
            "`lab/ideas.md`. Free text fields are sanitised but not "
            "structured — write prose."
        ),
        argv_template=[
            "idea", "append", "{idea_id}",
            "--theme", "{theme}",
            "--motivation", "{motivation}",
            "--sketch", "{sketch}",
        ],
        params=[
            ParamSpec(
                name="idea_id",
                pattern=_SAFE_TOKEN,
                label="Idea id (slug)",
                placeholder="loop-guard",
                help_text="Lowercase slug, hyphen-separated. Must be unique.",
            ),
            ParamSpec(
                name="theme",
                pattern=_IDEA_THEME,
                label="Theme",
                placeholder="Architecture | Runtime | Tools | Memory",
            ),
            ParamSpec(
                name="motivation",
                pattern=_SAFE_TEXT,
                label="Motivation",
                placeholder="Why this might be worth trying.",
            ),
            ParamSpec(
                name="sketch",
                pattern=_SAFE_LONG_TEXT,
                label="Sketch",
                placeholder="Rough outline of the change. Multi-line OK.",
            ),
        ],
        confirm_text=None,
        events=["lab-ideas-changed", "lab-pending-changed"],
    ),
    "roadmap-add": CommandSpec(
        cmd_id="roadmap-add",
        label="Add to Up next",
        description=(
            "Append a new human-curated entry to `## Up next` in "
            "`lab/roadmap.md`. Use `roadmap-suggest` instead for "
            "daemon/critic suggestions."
        ),
        argv_template=[
            "roadmap", "add", "{slug}",
            "--hypothesis", "{hypothesis}",
            "--plan", "{plan}",
            ["--idea", "{idea}"],
            ["--depends-on", "{depends_on}"],
            ["--cost", "{cost}"],
        ],
        params=[
            ParamSpec(
                name="slug",
                pattern=_SAFE_TOKEN,
                label="Slug",
                placeholder="tb2-runtime-shell-shim",
            ),
            ParamSpec(
                name="hypothesis",
                pattern=_SAFE_TEXT,
                label="Hypothesis",
                placeholder="What you expect to learn / change.",
            ),
            ParamSpec(
                name="plan",
                pattern=_SAFE_LONG_TEXT,
                label="Plan",
                placeholder="Concrete experimental setup. Multi-line OK.",
            ),
            ParamSpec(
                name="idea",
                pattern=_SAFE_TOKEN,
                label="Idea id (optional)",
                required=False,
                placeholder="loop-guard",
                help_text="Cross-link to the originating idea, if any.",
            ),
            ParamSpec(
                name="depends_on",
                pattern=_SAFE_TEXT,
                label="Depends on (optional)",
                required=False,
                placeholder="other-slug, another-slug",
                help_text="Slug list; the daemon will block on these.",
            ),
            ParamSpec(
                name="cost",
                pattern=_SAFE_TEXT,
                label="Cost (optional)",
                required=False,
                placeholder="~$3 / 30 min",
            ),
        ],
        confirm_text=None,
        events=["lab-roadmap-changed", "lab-pending-changed"],
    ),
    "roadmap-suggest": CommandSpec(
        cmd_id="roadmap-suggest",
        label="Suggest experiment",
        description=(
            "Append a daemon-style entry to `## Up next > ### Suggested`. "
            "Humans then promote one to the main queue."
        ),
        argv_template=[
            "roadmap", "suggest", "{slug}",
            "--hypothesis", "{hypothesis}",
            "--source", "{source}",
            ["--cost", "{cost}"],
        ],
        params=[
            ParamSpec(
                name="slug",
                pattern=_SAFE_TOKEN,
                label="Slug",
                placeholder="tb2-foo-bar",
            ),
            ParamSpec(
                name="hypothesis",
                pattern=_SAFE_TEXT,
                label="Hypothesis",
            ),
            ParamSpec(
                name="source",
                pattern=_SAFE_TEXT,
                label="Source",
                default="human:webui",
                placeholder="cross-experiment-critic@2026-04-18",
                help_text="Who/what is suggesting this.",
            ),
            ParamSpec(
                name="cost",
                pattern=_SAFE_TEXT,
                label="Cost (optional)",
                required=False,
            ),
        ],
        confirm_text=None,
        events=["lab-roadmap-changed", "lab-pending-changed"],
    ),
    "component-set-status": CommandSpec(
        cmd_id="component-set-status",
        label="Set component status",
        description=(
            "Unconditional status set (humans only — bypasses the "
            "forward-only bump lattice that `upsert` enforces)."
        ),
        argv_template=[
            "components", "set-status", "{component_id}", "{status}",
            ["--evidence", "{evidence}"],
        ],
        params=[
            ParamSpec(name="component_id", pattern=_SAFE_TOKEN, label="Component id"),
            ParamSpec(
                name="status",
                pattern=_COMPONENT_STATUS,
                label="Status",
                placeholder="proposed | experimental | branch | validated | rejected | superseded",
            ),
            ParamSpec(
                name="evidence",
                pattern=_SAFE_TEXT,
                label="Evidence (optional)",
                required=False,
                placeholder="[run](runs/experiments/<id>) or short note",
            ),
        ],
        confirm_text=(
            "Set status without the bump lattice? Humans only — make "
            "sure you actually want to skip the safety check."
        ),
        events=["lab-components-changed"],
    ),
    "component-upsert": CommandSpec(
        cmd_id="component-upsert",
        label="Add / update component",
        description=(
            "Insert a new component or update an existing one. "
            "Status bumps via this command are forward-only; use "
            "`component-set-status` for the unsafe bypass."
        ),
        argv_template=[
            "components", "upsert", "{component_id}",
            "--kind", "{kind}",
            ["--description", "{description}"],
            ["--status", "{status}"],
            ["--used-by", "{used_by}"],
            ["--evidence", "{evidence}"],
        ],
        params=[
            ParamSpec(name="component_id", pattern=_SAFE_TOKEN, label="Component id"),
            ParamSpec(
                name="kind",
                pattern=_COMPONENT_KIND,
                label="Kind",
                placeholder="Architecture | Runtime | Tools | Prompt | Model",
            ),
            ParamSpec(
                name="description",
                pattern=_SAFE_TEXT,
                label="Description (optional)",
                required=False,
            ),
            ParamSpec(
                name="status",
                pattern=_COMPONENT_STATUS,
                label="Status (optional)",
                required=False,
                placeholder="proposed | experimental | branch | validated | rejected | superseded",
                help_text="Forward-only bump; reject is the only sideways move.",
            ),
            ParamSpec(
                name="used_by",
                pattern=_USED_BY,
                label="Used by (optional)",
                required=False,
                placeholder="agent-1,agent-2",
                help_text="Comma-separated list of agent ids.",
            ),
            ParamSpec(
                name="evidence",
                pattern=_SAFE_TEXT,
                label="Evidence (optional)",
                required=False,
            ),
        ],
        confirm_text=None,
        events=["lab-components-changed"],
    ),
    # -- daemon control surface --------------------------------------------
    #
    # These mutate `runs/lab/daemon-state.json`, not markdown. They're
    # the buttons behind the /daemon page's mode toggle, the
    # per-roadmap-entry "Approve & run" affordance, and the cancel /
    # reset-failures actions on the active-tick panel. Identical to
    # the `lab daemon …` Typer commands by construction (we shell out
    # to them) so the audit log shape is the same as a CLI invocation.
    "daemon-mode": CommandSpec(
        cmd_id="daemon-mode",
        label="Set daemon mode",
        description=(
            "Switch the daemon between paused (does nothing), manual "
            "(runs only operator-approved entries), and autonomous "
            "(walks the queue automatically; exit gate still on)."
        ),
        argv_template=["daemon", "mode", "{mode}", "--actor", "{actor}"],
        params=[
            ParamSpec(
                name="mode",
                pattern=_DAEMON_MODE,
                label="Mode",
                placeholder="paused | manual | autonomous",
            ),
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text=None,
        events=["lab-daemon-changed", "lab-daemon-state-changed"],
    ),
    "daemon-approve": CommandSpec(
        cmd_id="daemon-approve",
        label="Approve & queue",
        description=(
            "Approve a roadmap slug for one tick. The daemon will pick "
            "it up on the next loop iteration (manual mode) and the "
            "approval is consumed afterwards."
        ),
        argv_template=["daemon", "approve", "{slug}", "--actor", "{actor}"],
        params=[
            ParamSpec(name="slug", pattern=_SAFE_TOKEN, label="Slug"),
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text=None,
        events=["lab-daemon-state-changed", "lab-roadmap-changed"],
    ),
    "daemon-revoke": CommandSpec(
        cmd_id="daemon-revoke",
        label="Revoke approval",
        description="Remove a slug from the approval list (no-op if absent).",
        argv_template=["daemon", "revoke", "{slug}", "--actor", "{actor}"],
        params=[
            ParamSpec(name="slug", pattern=_SAFE_TOKEN, label="Slug"),
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text=None,
        events=["lab-daemon-state-changed", "lab-roadmap-changed"],
    ),
    "daemon-cancel": CommandSpec(
        cmd_id="daemon-cancel",
        label="Cancel active tick",
        description=(
            "SIGTERM the active codex spawn (if any) and clear the "
            "active tick. Operator override — does NOT count toward "
            "the auto-demote gate."
        ),
        argv_template=["daemon", "cancel", "--actor", "{actor}"],
        params=[
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text="Cancel the active tick and SIGTERM the codex spawn?",
        events=["lab-daemon-state-changed", "lab-process-tree-changed"],
    ),
    "daemon-reset-failures": CommandSpec(
        cmd_id="daemon-reset-failures",
        label="Reset failure counter",
        description=(
            "Clear the recorded consecutive-failure count for a slug, "
            "so the next approval starts from zero (won't auto-demote "
            "until N more failures in a row)."
        ),
        argv_template=["daemon", "reset-failures", "{slug}", "--actor", "{actor}"],
        params=[
            ParamSpec(name="slug", pattern=_SAFE_TOKEN, label="Slug"),
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text=None,
        events=["lab-daemon-state-changed"],
    ),
    "daemon-reset-all-failures": CommandSpec(
        cmd_id="daemon-reset-all-failures",
        label="Reset all failure counters",
        description=(
            "Clear every recorded failure counter at once. Useful "
            "after fixing a host-level cause (PATH, credentials) "
            "that broke a batch of slugs simultaneously."
        ),
        argv_template=["daemon", "reset-all-failures", "--actor", "{actor}"],
        params=[
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text=(
            "Clear ALL failure counters? After this, every approved "
            "slug starts from zero — no auto-demote until N consecutive "
            "failures."
        ),
        events=["lab-daemon-state-changed"],
    ),
    "daemon-clear-history": CommandSpec(
        cmd_id="daemon-clear-history",
        label="Clear tick history",
        description=(
            "Wipe the tick-history ring buffer. Purely cosmetic — "
            "the daemon never reads history back into its decision "
            "loop, but the cockpit's 'Recent ticks' panel renders "
            "from it. Useful for starting fresh after debugging."
        ),
        argv_template=["daemon", "clear-history", "--actor", "{actor}"],
        params=[
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
            ),
        ],
        confirm_text="Wipe the tick history? This cannot be undone.",
        events=["lab-daemon-state-changed"],
    ),
    # -- on-disk run cleanup -----------------------------------------------
    #
    # `runs prune` and friends. Strict regex on age_hours so a typo
    # ("24h") can't squeak past Typer; force is a literal "true"/"false"
    # toggle so the form maps cleanly to a checkbox.
    "runs-prune": CommandSpec(
        cmd_id="runs-prune",
        label="Prune unfinished runs",
        description=(
            "Delete runs/experiments/<id>/ directories that have NO "
            "results/summary.md AND haven't been touched for at least "
            "--age-hours hours. Frees disk and unclutters the file "
            "list. The default 1 h gate prevents racing a live run; "
            "for the more aggressive --force/--age-hours=0 mode, use "
            "the CLI directly."
        ),
        argv_template=[
            "runs", "prune",
            "--age-hours", "{age_hours}",
            "--actor", "{actor}",
        ],
        params=[
            ParamSpec(
                name="age_hours",
                # Bounded to >=1 here on purpose: the CLI's --force
                # gate exists for a reason, and exposing the unsafe
                # path through a web button would hide that gate.
                pattern=re.compile(r"^(?:[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$"),
                label="Min age (hours)",
                default="1",
                placeholder="1",
                help_text=(
                    "Dirs younger than this stay. Use the CLI with "
                    "--force for anything below 1h."
                ),
            ),
            ParamSpec(
                name="actor",
                pattern=_SAFE_ACTOR,
                label="Actor",
                default="human:webui",
                required=False,
            ),
        ],
        confirm_text=(
            "Permanently delete every unfinished experiment run "
            "directory older than the threshold? This cannot be undone."
        ),
        danger=True,
        events=["lab-runs-changed", "lab-daemon-state-changed"],
    ),
}


# ---------------------------------------------------------------------------
# Prechecks
# ---------------------------------------------------------------------------


def _precheck_kill_process(params: dict[str, str]) -> None:
    """Refuse PIDs that are not descendants of the orchestrator daemon.

    This is the only spec-level safety check we ship today: ``kill``
    is too sharp a tool to expose to a web form without an ownership
    test. Walks the parent chain via ``psutil`` and aborts unless an
    ancestor matches the orchestrator's main_pid.

    Failure modes (each surfaces as :class:`CommandError`):
    - daemon isn't running (no ancestor to match)
    - PID doesn't exist
    - PID is the orchestrator itself (use ``daemon-stop`` instead)
    - PID is the web UI itself (use ``service-restart openharness-lab``)
    """
    import psutil

    pid = int(params["pid"])

    # Refuse to kill the web UI's own pid — operator should use
    # service-restart, which goes through systemd cleanly.
    self_pid = os.getpid()
    if pid in {self_pid, os.getppid()}:
        raise CommandError(
            "refusing to kill the web UI's own process; "
            "use `service-restart openharness-lab` instead"
        )

    daemon_status = labsvc.status("openharness-daemon")
    daemon_pid = daemon_status.main_pid
    if daemon_pid is None:
        raise CommandError(
            "orchestrator daemon is not running; nothing to clean up"
        )

    if pid == daemon_pid:
        raise CommandError(
            "refusing to kill the orchestrator pid directly; "
            "use `daemon-stop` (or `daemon-restart`) instead"
        )

    try:
        target = psutil.Process(pid)
        ancestors = {p.pid for p in target.parents()}
    except psutil.NoSuchProcess as exc:
        raise CommandError(f"pid {pid} does not exist") from exc
    except psutil.AccessDenied as exc:
        raise CommandError(
            f"insufficient permissions to inspect pid {pid}"
        ) from exc

    if daemon_pid not in ancestors:
        raise CommandError(
            f"pid {pid} is not a descendant of the orchestrator daemon "
            f"(pid {daemon_pid}); refusing to kill it. The web UI only "
            "manages processes the daemon spawned."
        )


# Wire precheck into the spec — done here (not in the dict literal
# above) to keep the imports of psutil lazy and the registry literal
# easier to read.
COMMANDS["kill-process"].precheck = _precheck_kill_process


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


_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_token(token: str, params: dict[str, str]) -> str:
    """Substitute every ``{name}`` placeholder inside ``token``.

    Whole-token placeholders (``"{slug}"``) work as before; mixed
    tokens (``"{unit}.service"``, ``"--name={slug}"``) are now
    interpolated too. We deliberately don't support format specs or
    nested expressions — the regex matches a bare identifier — so
    accidental ``{}`` in user-controlled config can't introduce new
    Python-format-mini-language behaviour.
    """
    if "{" not in token:
        return token

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in params:
            raise CommandError(f"argv template references missing param {key!r}")
        return params[key]

    return _PLACEHOLDER_RE.sub(_sub, token)


def _placeholder_keys(token: str) -> list[str]:
    """Return the ``{name}`` identifiers used inside ``token``."""
    return _PLACEHOLDER_RE.findall(token)


def _build_argv(spec: CommandSpec, params: dict[str, str]) -> list[str]:
    argv: list[str] = []
    for token in spec.argv_template:
        if isinstance(token, list):
            # Optional flag group — include only if every placeholder
            # inside it resolved to a value. Otherwise drop the entire
            # group so the CLI gets the clean default behaviour.
            placeholders: list[str] = []
            for inner in token:
                placeholders.extend(_placeholder_keys(inner))
            if all(p in params for p in placeholders):
                for inner in token:
                    argv.append(_resolve_token(inner, params))
            continue
        argv.append(_resolve_token(token, params))
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

    # Precheck *after* param validation so the hook gets clean values.
    # Raising :class:`CommandError` aborts before subprocess spawn; the
    # FastAPI route surfaces it as a 400.
    if spec.precheck is not None:
        spec.precheck(params)

    cli_args = _build_argv(spec, params)

    # Resolve the leading binary via shutil.which so an absent
    # systemctl / kill / uv on the web UI's PATH yields a clean exit
    # 127 rather than a FileNotFoundError leaking past the route. The
    # rest of argv_prefix passes through verbatim.
    if not spec.argv_prefix:
        raise CommandError(
            f"spec {cmd_id!r} has empty argv_prefix; refusing to spawn"
        )
    bin_name = spec.argv_prefix[0]
    bin_path = shutil.which(bin_name) or bin_name
    argv = [bin_path, *spec.argv_prefix[1:], *cli_args]

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
        # Binary not on PATH inside the server env. Surface, don't crash.
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
