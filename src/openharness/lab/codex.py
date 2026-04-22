"""Uniform `codex exec` adapter for the lab orchestrator.

Every multi-step "thinking" task in the lab loop (variant
implementation, the four critic skills, task-features extraction,
plus the small mechanical lab-* skills) is invoked through this
adapter so we have one failure mode for agent execution and one
shape of audit trail.

Three knob layers, in increasing specificity:

1. **Codex CLI defaults** (what the user has in `~/.codex/config.toml`).
   We never read those directly; they are simply the fallback if we
   omit a flag.
2. **Lab defaults** (`CodexConfig`). One set of sane defaults for the
   whole orchestrator process. The runner overrides things like
   `max_concurrency` from CLI flags. These cover model, reasoning
   effort, reasoning summary, sandbox, ephemeral, timeout.
3. **Per-skill profile** (`SKILL_PROFILES[skill_id]`). Tuned per skill
   based on its job: bulk graders use `gpt-5.4-mini` at low effort,
   the cross-experiment critic uses `high` effort and runs as a
   singleton, the variant implementer gets a longer timeout, etc.
   Anything left as `None` falls back to the lab default.

The 0.121 codex CLI exposes:

  -m / --model              <id>             # picked per skill below
  -c key=value                              # TOML-typed config overrides
  -s / --sandbox            <mode>           # default workspace-write
  --full-auto                                # alias for workspace-write + auto-approve
  --ephemeral                                # no session file written to ~/.codex/sessions
  --add-dir <dir>                            # extra writable dirs (escape hatch)
  --output-schema <file>                     # JSON Schema constraining final assistant message
  -C / --cd <dir>                            # repo root
  --skip-git-repo-check                      # we always set this
  --json                                     # emit jsonl event stream to stdout
  -o <file>                                  # write last assistant message to file

The two `-c` overrides we care about today:

  model_reasoning_effort   one of: none | minimal | low | medium | high | xhigh
  model_reasoning_summary  one of: auto | concise | detailed | none

Skill discovery: codex >= 0.121 picks up `.agents/skills/` automatically
when invoked with `--cd <repo>`. We additionally inline the SKILL.md
body into the prompt for the version-pinned, hand-on-the-wheel safety
of "this is exactly what the agent saw".

Logs: per-spawn log at `runs/lab/logs/<utc>__<skill>__<short_spawn_id>.log`
with prompt, JSONL event stream, last_message, exit code, and the full
effective settings (model, effort, summary, etc.) so each run is
self-describing. A row also lands in the `spawns` DuckDB table.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from openharness.lab import critic_io
from openharness.lab.paths import (
    LAB_LOGS_DIR,
    ORCHESTRATOR_LOCK_PATH,
    REPO_ROOT,
    ensure_lab_runs_dir,
)

logger = logging.getLogger(__name__)

SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
SKILL_SCHEMAS_DIR = REPO_ROOT / "schemas" / "skills"
# Timeouts here are *safety nets against hung subprocesses*, not
# throughput knobs. The autonomous loop runs overnight; the human
# wants the highest-quality answer, not the fastest one. We pick
# upper bounds that a healthy spawn never approaches but a wedged
# one can't sit forever on (which would block the orchestrator
# pool and stall the daemon).
DEFAULT_TIMEOUT_SEC = 60 * 60 * 6  # 6 h default; per-skill overrides below.
DEFAULT_MAX_CONCURRENCY = 4

# Allowed values per the 0.121 CLI surface; we validate up front to
# catch typos in the per-skill profile before shelling out.
_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
_VALID_REASONING_SUMMARIES = {"auto", "concise", "detailed", "none"}
_VALID_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}


class CodexAdapterError(RuntimeError):
    """Adapter-level failure (skill not found, codex missing, etc.)."""


@dataclass(slots=True)
class SpawnResult:
    spawn_id: str
    skill: str
    args: list[str]
    exit_code: int
    log_path: Path
    last_message: str | None
    started_at: datetime
    finished_at: datetime
    duration_sec: float
    effective_settings: dict[str, object] = field(default_factory=dict)
    cost_usd_estimate: float | None = None
    parent_run_dir: Path | None = None
    notes: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class CodexConfig:
    """Process-wide defaults shared across spawns.

    Not slotted: we lazily attach a `_semaphore` after construction.
    """

    binary: str = "codex"
    cwd: Path = REPO_ROOT

    # Model and reasoning defaults (per-skill profiles can override).
    # The lab runs autonomously and the human optimizes for accuracy
    # and signal-density, not latency: default to the flagship at
    # `high` effort with `detailed` reasoning summaries (so a human
    # auditing logs can reconstruct the reasoning when something
    # surprising lands).
    default_model: str = "gpt-5.4"
    default_reasoning_effort: str = "high"
    default_reasoning_summary: str = "detailed"

    # Sandbox / runtime defaults.
    #
    # `danger-full-access` is the right default for the autonomous
    # lab loop: it runs unattended on a dedicated machine, so any
    # OS-level sandbox restriction (landlock/seccomp on Linux,
    # seatbelt on macOS) becomes a silent failure with no human to
    # approve an override. The argv builder translates this to
    # `--dangerously-bypass-approvals-and-sandbox` (which is mutually
    # exclusive with `--full-auto`). Per-skill profiles can downgrade
    # to `workspace-write` for ad-hoc invocations where confinement
    # is wanted; in that case `default_full_auto` controls whether
    # we add `--full-auto` (sandbox + auto-approve) or just `-s`
    # (sandbox + ask, which is unusable in the autonomous loop).
    default_sandbox: str = "danger-full-access"
    default_full_auto: bool = True
    default_ephemeral: bool = True
    default_timeout_sec: int = DEFAULT_TIMEOUT_SEC

    # Pool + lock + telemetry.
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    enforce_orchestrator_lock: bool = False
    record_in_db: bool = True

    # Free-form escape hatch — appended last so it can override anything.
    extra_codex_args: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.default_reasoning_effort not in _VALID_REASONING_EFFORTS:
            raise CodexAdapterError(
                f"Invalid default_reasoning_effort {self.default_reasoning_effort!r}; "
                f"expected one of {sorted(_VALID_REASONING_EFFORTS)}"
            )
        if self.default_reasoning_summary not in _VALID_REASONING_SUMMARIES:
            raise CodexAdapterError(
                f"Invalid default_reasoning_summary {self.default_reasoning_summary!r}"
            )
        if self.default_sandbox not in _VALID_SANDBOXES:
            raise CodexAdapterError(
                f"Invalid default_sandbox {self.default_sandbox!r}"
            )
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)

    @property
    def semaphore(self) -> threading.BoundedSemaphore:
        return self._semaphore


@dataclass(frozen=True)
class SkillProfile:
    """Per-skill overrides for codex invocations.

    Any field left as `None` falls back to the corresponding
    `CodexConfig.default_*`. Only declare what's actually different
    from the lab default — keeps the table easy to read.
    """

    model: str | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    sandbox: str | None = None
    full_auto: bool | None = None
    ephemeral: bool | None = None
    timeout_sec: int | None = None

    # When True, only one spawn of this skill runs at a time
    # process-wide (in addition to the global semaphore). Use for
    # skills whose work is intrinsically global (cross-experiment-critic
    # rebuilds the components_perf table, so two of them stomping on
    # each other is a bug).
    singleton: bool = False

    # Optional path (repo-relative or absolute) to a JSON Schema that
    # constrains the model's *final assistant message*. Only useful
    # when a skill returns its payload in last_message rather than
    # piping it to a `uv run lab` subcommand. If None, the adapter
    # auto-detects `schemas/skills/<skill_id>.json`.
    output_schema: str | None = None

    # Notes for humans reading this table. Not used by the adapter.
    notes: str = ""

    # Free-form extra args appended after the standard ones. Use
    # sparingly; prefer adding a typed field.
    extra_args: tuple[str, ...] = ()


# -----------------------------------------------------------------------
# Per-skill profile table.
#
# Scope: this table covers ONLY skills the orchestrator
# (`runner.py`) invokes through this adapter. Human-driven skills
# (`lab-propose-idea`, `lab-graduate-component`) live in the same
# `.agents/skills/` tree but are invoked from Cursor against its
# own model — they never go through `codex exec`, so giving them a
# profile here would be misleading. The current orchestrator-side
# set is exactly:
#
#   lab-design-variant, lab-implement-variant, lab-finalize-pr,
#   trial-critic, task-features, experiment-critic,
#   cross-experiment-critic, lab-plan-next
#
# (The legacy `lab-run-experiment` skill is gone — its
# responsibilities were split across the deterministic
# `runner._process_entry_phased` plus the three new `lab-*-variant`
# skills, with the actual run-kickoff moved into
# `phase_run.run_experiment`.)
#
# Design philosophy: the human optimizes for SIGNAL DENSITY, not
# throughput or token cost. The lab runs overnight; an extra hour
# of model thinking is free, an unreliable verdict is expensive
# (every downstream decision compounds the noise). So:
#
#   - The whole tower of analysis sits on top of `trial-critic`'s
#     verdicts. Use the flagship at `medium` effort — going lower
#     poisons everything above; going xhigh per trial doesn't give
#     enough additional signal per token to justify it at scale.
#   - `experiment-critic` aggregates dozens of trials into a leg
#     comparison that drives the next experiment's design. `high`.
#   - `cross-experiment-critic` is the apex spawn: it rewrites
#     `components_perf`, proposes follow-up ideas (writing to
#     `lab/ideas.md > ## Auto-proposed`), and shapes the entire
#     roadmap. `xhigh`, `detailed` summary, singleton.
#   - `lab-design-variant` is read-only and writes ONE design.md.
#     Cheap thinking budget; medium effort. The downstream
#     implement phase has the design as its contract, so a sloppy
#     design is a guaranteed implement-phase failure — but a
#     "good enough" design at high effort wastes thinking time
#     when the implement phase will catch holes anyway. Sandbox:
#     read-only.
#   - `lab-implement-variant` writes code in a worktree. Code
#     quality directly shapes the variant being measured; `high`
#     effort. Sandbox: workspace-write scoped to the worktree.
#   - `lab-finalize-pr` is mostly mechanical (push, gh pr create,
#     CLI mutation). `low` effort — prompt explicitly disables
#     creative work here. Sandbox: workspace-write.
#   - `lab-plan-next` is the only mechanical skill the orchestrator
#     invokes — closing the loop on the roadmap entry. Smarter
#     models do not add signal here; mini+low keeps logs clean.
#
# Timeouts are SAFETY NETS (catch a wedged subprocess), not quality
# knobs. Each upper bound is generous enough that a healthy spawn
# at the configured effort level never gets near it, but tight
# enough that a hung process can't sit on a pool slot forever.
# -----------------------------------------------------------------------
SKILL_PROFILES: dict[str, SkillProfile] = {
    # --- bulk grading: 1 invocation per trial / per task_checksum ---
    "trial-critic": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="medium",
        reasoning_summary="concise",
        timeout_sec=60 * 60 * 2,  # 2h safety net per trial
        notes=(
            "Bulk per-trial grader. Use the flagship: every analytical "
            "decision downstream of this is built on its verdicts, so "
            "the marginal cost of a smarter call is repaid many times."
        ),
    ),
    "task-features": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="medium",
        reasoning_summary="concise",
        timeout_sec=60 * 60 * 2,
        notes=(
            "One-shot semantic feature extraction per task_checksum. "
            "Output feeds clustering and routing decisions, so feature "
            "quality is leverage."
        ),
    ),

    # --- per-experiment aggregation ---
    "experiment-critic": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="high",
        reasoning_summary="detailed",
        timeout_sec=60 * 60 * 6,
        # Per-task comparisons are independent; let the agent fan out
        # via codex's stable multi_agent feature so wall-clock stays
        # bounded as N tasks grows. Synthesis remains in the parent.
        extra_args=("--enable", "multi_agent"),
        notes=(
            "Aggregates per-trial critiques across legs of one experiment "
            "and decides the winning configuration. High effort; per-task "
            "comparisons are parallelized via codex multi_agent."
        ),
    ),

    # --- cross-experiment analysis (apex spawn; singleton) ---
    "cross-experiment-critic": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="xhigh",
        reasoning_summary="detailed",
        timeout_sec=60 * 60 * 12,
        singleton=True,
        # Decompose along components / clusters via subagents. The
        # final synthesis (follow-up ideas) stays in the parent agent
        # so we get one coherent narrative.
        extra_args=("--enable", "multi_agent"),
        notes=(
            "Apex spawn: refreshes components_perf files, identifies "
            "cross-experiment patterns, and proposes follow-up ideas "
            "that shape the entire roadmap. Worth the maximum effort. "
            "Singleton because the apex snapshot is process-global. "
            "Per-component analyses are parallelized via codex multi_agent."
        ),
    ),

    # --- phase 1: design the variant ---
    # Sandbox is workspace-write (not read-only) because the output file
    # `runs/lab/state/<slug>/design.md` lives inside the workspace and the
    # read-only sandbox blocks writes to it. Source-file edits are
    # prevented by the SKILL.md contract ("No source-file edits") rather
    # than the sandbox — the same pattern used by the implement phase.
    "lab-design-variant": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="medium",
        reasoning_summary="concise",
        timeout_sec=60 * 60,  # 1h cap; designs typically settle in <15min
        notes=(
            "Phase 1 of the lab pipeline. Reads the codebase + idea "
            "and writes runs/lab/state/<slug>/design.md. Medium effort: "
            "the implement phase catches design gaps anyway, so paying "
            "high-effort thinking for marginal design polish is waste."
        ),
    ),

    # --- phase 2: implement the variant in the worktree ---
    "lab-implement-variant": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="high",
        reasoning_summary="detailed",
        timeout_sec=60 * 60 * 4,
        notes=(
            "Phase 2 of the lab pipeline. Reads design.md and "
            "applies it inside ../OpenHarness.worktrees/lab-<slug>/. "
            "Sandbox=danger-full-access (the orchestrator's parent "
            "default) because the implement phase needs to run "
            "validation scripts (uv, pytest) inside the worktree. "
            "High effort: variant code quality is what the experiment "
            "actually measures."
        ),
    ),

    # --- phase 5: open (or skip) the PR after the verdict is in ---
    "lab-finalize-pr": SkillProfile(
        model="gpt-5.4-mini",
        reasoning_effort="low",
        reasoning_summary="concise",
        # workspace-write is enough — only git/gh + `uv run lab`. No
        # code edits expected (and the prompt forbids them).
        sandbox="workspace-write",
        full_auto=True,
        timeout_sec=60 * 30,  # 30min cap; PR creation is fast
        notes=(
            "Phase 5 of the lab pipeline. Pushes the experiment "
            "branch and opens a PR (or skips, on reject/noop), then "
            "rewrites the journal Branch bullet via "
            "`lab experiments set-branch`. Mostly mechanical — mini "
            "model at low effort is enough; the prompt is explicit "
            "that creative work is forbidden here."
        ),
    ),

    # --- mechanical roadmap nudge (orchestrator-invoked) ---
    # NOTE: `reasoning_effort="minimal"` is rejected by the API when
    # the agent has the `web_search` tool, which codex 0.121 registers
    # by default under --full-auto / --dangerously-bypass-*. So the
    # practical floor for our skills is `low`. If we ever wire up a
    # tool-suppressing config (e.g. `-c features.web_search=false`),
    # this can drop to minimal.
    "lab-plan-next": SkillProfile(
        model="gpt-5.4-mini",
        reasoning_effort="low",
        reasoning_summary="none",
        timeout_sec=60 * 60,
        notes=(
            "Mechanical: moves the just-finished roadmap entry to "
            "## Done. More model intelligence does not improve the "
            "outcome; the work is structural, not analytical."
        ),
    ),

    # --- tree-aware planner (orchestrator-invoked, post-tree-apply) ---
    "lab-reflect-and-plan": SkillProfile(
        model="gpt-5.4",
        reasoning_effort="high",
        reasoning_summary="detailed",
        timeout_sec=4 * 60 * 60,
        notes=(
            "Reads the current configuration tree + the latest "
            "journal entries + the cross-experiment snapshot, and "
            "writes 0..N follow-up entries to "
            "`roadmap.md > ## Up next > ### Suggested` and "
            "`ideas.md > ## Auto-proposed`. Designs the next "
            "experiment — high-signal, judgment-heavy. Worth "
            "spending tokens for accuracy because each output "
            "compounds into the priority queue."
        ),
    ),
    # Skills NOT registered here, intentionally:
    #   - lab-propose-idea: human-driven only. Ideas under
    #     `lab/ideas.md > ## Proposed` are curated by the human
    #     via Cursor; the orchestrator never proposes ideas.
    #     (cross-experiment-critic and lab-reflect-and-plan write
    #     into ## Auto-proposed directly via `uv run lab idea
    #     auto-propose`, not via this skill.)
    #   - lab-graduate-component: human-driven only. The
    #     `Graduate` verdict is the *only* asymmetric action in
    #     the loop — the daemon stages it via `lab tree apply`,
    #     and a human runs `uv run lab graduate confirm <slug>`
    #     (or this skill in Cursor) to actually swap the trunk.
    #   - lab-operator: a Cursor-side meta-skill that drives
    #     `uv run lab daemon ...`; never invoked from the loop.
}


# Per-skill singleton locks (lazily allocated, keyed by skill_id).
_SKILL_SINGLETON_LOCKS: dict[str, threading.Lock] = {}
_SKILL_SINGLETON_LOCKS_GUARD = threading.Lock()


def _singleton_lock_for(skill_id: str) -> threading.Lock:
    with _SKILL_SINGLETON_LOCKS_GUARD:
        lock = _SKILL_SINGLETON_LOCKS.get(skill_id)
        if lock is None:
            lock = threading.Lock()
            _SKILL_SINGLETON_LOCKS[skill_id] = lock
    return lock


# ----- skill discovery ------------------------------------------------------


def skill_path(skill_id: str) -> Path:
    """Return the SKILL.md path for `skill_id`, or raise."""
    if not SKILLS_DIR.is_dir():
        raise CodexAdapterError(
            f"Shared skills dir not found: {SKILLS_DIR}. Codex / Cursor expect "
            "skills to live under .agents/skills/<id>/SKILL.md."
        )
    candidate = SKILLS_DIR / skill_id / "SKILL.md"
    if not candidate.is_file():
        raise CodexAdapterError(f"Skill not found: {candidate}")
    return candidate


def list_skills() -> list[str]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d.name for d in SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )


def _ensure_skill_path() -> None:
    if not SKILLS_DIR.is_dir():
        raise CodexAdapterError(
            f"Skills directory missing: {SKILLS_DIR}. The lab pipeline "
            "expects skills at this path (shared with Cursor)."
        )


# ----- precondition checks --------------------------------------------------


def _check_binary(cfg: CodexConfig) -> None:
    if shutil.which(cfg.binary) is None:
        raise CodexAdapterError(
            f"`{cfg.binary}` not found on PATH. Install Codex CLI before "
            "running the orchestrator."
        )


def _check_auth() -> None:
    """Refuse to spawn unless codex is authenticated via ChatGPT subscription.

    Hard rule for this project: never use OPENAI_API_KEY for codex.
    The ChatGPT-subscription path has the generous quota the user pays
    for; the API-key path bills against a separate (and easily
    exhausted) OpenAI Platform balance and has burned us once already.

    Concretely:
      1. ~/.codex/auth.json must exist and report `auth_mode == "chatgpt"`.
         (Set by `codex login` → "Sign in with ChatGPT".)
      2. We do NOT accept OPENAI_API_KEY as a fallback. Even if the env
         var is set, we ignore it (and `_build_env` strips it from the
         child process so codex itself can't see it either).
    """
    auth_file = Path.home() / ".codex" / "auth.json"
    if not auth_file.is_file():
        raise CodexAdapterError(
            "Codex auth missing: ~/.codex/auth.json not found. Run "
            "`codex login` and pick 'Sign in with ChatGPT'. "
            "We do NOT accept OPENAI_API_KEY for the lab orchestrator."
        )
    try:
        payload = json.loads(auth_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexAdapterError(
            f"Could not read {auth_file}: {exc}. Re-run `codex login` "
            "(pick 'Sign in with ChatGPT')."
        ) from exc
    mode = payload.get("auth_mode") or payload.get("mode")
    if mode != "chatgpt":
        raise CodexAdapterError(
            f"Codex is authenticated via auth_mode={mode!r}; the lab "
            "orchestrator REQUIRES auth_mode='chatgpt' (your ChatGPT "
            "subscription has the quota; the API-key path bills against "
            "a separate exhaustible balance and is forbidden here). "
            "Run `codex logout && codex login` and pick 'Sign in with "
            "ChatGPT'."
        )


def _check_orchestrator_lock(cfg: CodexConfig, *, expected_owner_pid: int | None) -> None:
    if not cfg.enforce_orchestrator_lock:
        return
    if not ORCHESTRATOR_LOCK_PATH.is_file():
        return
    try:
        owner = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return
    owner_pid = owner.get("pid")
    if expected_owner_pid is not None and owner_pid == expected_owner_pid:
        return
    raise CodexAdapterError(
        f"Another orchestrator holds the lock at {ORCHESTRATOR_LOCK_PATH}: "
        f"{owner!r}. Stop it first (`uv run lab daemon stop`) or remove the "
        "stale lock if you know it's safe."
    )


# ----- profile resolution + argv construction ------------------------------


def get_profile(skill_id: str, *, override: SkillProfile | None = None) -> SkillProfile:
    """Public hook so callers (e.g. `uv run lab analyze`) can fetch and
    optionally narrow a profile for a one-off invocation."""
    base = SKILL_PROFILES.get(skill_id, SkillProfile())
    if override is None:
        return base
    # Replace only the fields the override actually sets (non-default).
    patch: dict[str, object] = {}
    for f, default in (
        ("model", None), ("reasoning_effort", None),
        ("reasoning_summary", None), ("sandbox", None),
        ("full_auto", None), ("ephemeral", None),
        ("timeout_sec", None), ("singleton", False),
        ("output_schema", None), ("notes", ""),
        ("extra_args", ()),
    ):
        v = getattr(override, f)
        if v != default:
            patch[f] = v
    return replace(base, **patch) if patch else base


def _resolve_output_schema(skill_id: str, profile: SkillProfile) -> Path | None:
    """Locate the JSON Schema for a skill's final message, if any.

    Order: explicit `profile.output_schema` (resolved against the repo
    root if relative) → `schemas/skills/<skill_id>.json` if it exists →
    None.
    """
    if profile.output_schema:
        p = Path(profile.output_schema)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.is_file():
            raise CodexAdapterError(
                f"output_schema not found for skill {skill_id!r}: {p}"
            )
        return p
    auto = SKILL_SCHEMAS_DIR / f"{skill_id}.json"
    return auto if auto.is_file() else None


def effective_settings(skill_id: str, cfg: CodexConfig, profile: SkillProfile | None = None) -> dict[str, object]:
    """Return the resolved knobs for this spawn (for logging / debugging)."""
    profile = profile or get_profile(skill_id)
    schema = _resolve_output_schema(skill_id, profile)
    return {
        "skill": skill_id,
        "model": profile.model or cfg.default_model,
        "reasoning_effort": profile.reasoning_effort or cfg.default_reasoning_effort,
        "reasoning_summary": profile.reasoning_summary or cfg.default_reasoning_summary,
        "sandbox": profile.sandbox or cfg.default_sandbox,
        "full_auto": cfg.default_full_auto if profile.full_auto is None else profile.full_auto,
        "ephemeral": cfg.default_ephemeral if profile.ephemeral is None else profile.ephemeral,
        "timeout_sec": profile.timeout_sec or cfg.default_timeout_sec,
        "singleton": profile.singleton,
        "output_schema": str(schema) if schema else None,
    }


def _build_argv(
    skill_id: str,
    cfg: CodexConfig,
    profile: SkillProfile,
    *,
    last_msg_path: Path,
    schema_path: Path | None,
) -> list[str]:
    """Translate (cfg + profile) into the actual `codex exec ...` argv."""
    model = profile.model or cfg.default_model
    effort = profile.reasoning_effort or cfg.default_reasoning_effort
    summary = profile.reasoning_summary or cfg.default_reasoning_summary
    sandbox = profile.sandbox or cfg.default_sandbox
    full_auto = cfg.default_full_auto if profile.full_auto is None else profile.full_auto
    ephemeral = cfg.default_ephemeral if profile.ephemeral is None else profile.ephemeral

    if effort not in _VALID_REASONING_EFFORTS:
        raise CodexAdapterError(
            f"Invalid reasoning_effort {effort!r} for skill {skill_id!r}; "
            f"expected one of {sorted(_VALID_REASONING_EFFORTS)}"
        )
    if summary not in _VALID_REASONING_SUMMARIES:
        raise CodexAdapterError(
            f"Invalid reasoning_summary {summary!r} for skill {skill_id!r}"
        )
    if sandbox not in _VALID_SANDBOXES:
        raise CodexAdapterError(
            f"Invalid sandbox {sandbox!r} for skill {skill_id!r}"
        )

    argv: list[str] = [
        cfg.binary, "exec",
        "--json",
        "--cd", str(cfg.cwd),
        "--skip-git-repo-check",
        "-o", str(last_msg_path),
        "-m", model,
        # `-c key=value` values are parsed as TOML; quote strings so
        # they round-trip as TOML strings rather than bare identifiers.
        "-c", f'model_reasoning_effort="{effort}"',
        "-c", f'model_reasoning_summary="{summary}"',
    ]

    # Sandbox + approval mode. Three valid combinations for our
    # autonomous loop:
    #   - danger-full-access -> --dangerously-bypass-approvals-and-sandbox
    #     (mutually exclusive with --full-auto and -s; this is the
    #     default for the autonomous loop on a dedicated machine)
    #   - workspace-write + full_auto=True -> --full-auto
    #     (the codex alias for "sandboxed write inside cwd + auto-approve")
    #   - any other sandbox -> -s <mode> only
    #     (the agent will then prompt for approvals, which means the
    #     skill will hang autonomously; only useful for hand-driven
    #     invocations via profile_override)
    if sandbox == "danger-full-access":
        argv.append("--dangerously-bypass-approvals-and-sandbox")
    elif full_auto and sandbox == "workspace-write":
        argv.append("--full-auto")
    else:
        argv += ["-s", sandbox]

    if ephemeral:
        argv.append("--ephemeral")

    if schema_path is not None:
        argv += ["--output-schema", str(schema_path)]

    argv += list(profile.extra_args)
    argv += list(cfg.extra_codex_args)

    # Prompt comes from stdin via "-".
    argv.append("-")
    return argv


# ----- prompt construction --------------------------------------------------


_PROMPT_TEMPLATE_OK_REFUSE = """\
You are running the `{skill_id}` skill non-interactively from the
OpenHarness lab orchestrator. Read the skill instructions below and
execute them against the arguments at the top.

Arguments (positional, in order):
{args_block}

When you are done, your FINAL message must start with one of:
  OK; <one-line summary>
  REFUSE; <reason>

Do not append any trailing text after that line. The orchestrator
parses your final message verbatim.

--- BEGIN SKILL: {skill_id} ---
{skill_body}
--- END SKILL: {skill_id} ---
"""


_PROMPT_TEMPLATE_SCHEMA = """\
You are running the `{skill_id}` skill non-interactively from the
OpenHarness lab orchestrator. Read the skill instructions below and
execute them against the arguments at the top.

Arguments (positional, in order):
{args_block}

Your FINAL assistant message MUST be a single JSON document that
validates against the attached --output-schema. Do not include any
prose, markdown fences, or commentary outside the JSON.

--- BEGIN SKILL: {skill_id} ---
{skill_body}
--- END SKILL: {skill_id} ---
"""


def _render_prompt(skill_id: str, args: Sequence[str], *, schema_path: Path | None) -> str:
    body = skill_path(skill_id).read_text()
    if args:
        args_block = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(args))
    else:
        args_block = "  (no arguments)"
    template = _PROMPT_TEMPLATE_SCHEMA if schema_path is not None else _PROMPT_TEMPLATE_OK_REFUSE
    return template.format(skill_id=skill_id, args_block=args_block, skill_body=body)


# ----- subprocess driver ----------------------------------------------------


def _new_spawn_id() -> str:
    return uuid.uuid4().hex[:12]


def _log_path_for(skill_id: str, spawn_id: str) -> Path:
    LAB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_skill = skill_id.replace("/", "_")
    return LAB_LOGS_DIR / f"{ts}__{safe_skill}__{spawn_id}.log"


def _record_spawn(result: SpawnResult, *, parent_run_dir: Path | None) -> None:
    """Write per-spawn telemetry as a file under `runs/lab/spawns/`.

    We deliberately do NOT touch DuckDB here. The previous DB-write
    raced with the children's writes (children call `uv run lab
    write-*` which hits its own writer lock), causing telemetry loss
    even with retries. Files have no concurrency story to lose;
    `uv run lab ingest-critiques` rolls them into the `spawns` table
    on demand.
    """
    record = {
        "spawn_id": result.spawn_id,
        "skill": result.skill,
        "args": list(result.args),
        "cwd": str(REPO_ROOT),
        "log_path": str(result.log_path),
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "exit_code": result.exit_code,
        "cost_usd_estimate": result.cost_usd_estimate,
        "parent_run_dir": str(parent_run_dir) if parent_run_dir else None,
        "notes": result.notes,
        "effective_settings": result.effective_settings,
        "duration_sec": result.duration_sec,
        "last_message": result.last_message,
    }
    try:
        critic_io.write_spawn_record(record)
    except Exception as exc:  # pragma: no cover - telemetry, shouldn't break runs
        logger.warning("failed to write spawn record %s: %s", result.spawn_id, exc)


def _parse_last_message(text: str) -> str | None:
    text = (text or "").strip()
    return text or None


def run(
    skill_id: str,
    args: Sequence[str] = (),
    *,
    cfg: CodexConfig | None = None,
    profile_override: SkillProfile | None = None,
    parent_run_dir: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    expected_orchestrator_pid: int | None = None,
) -> SpawnResult:
    """Run one skill via `codex exec`. Blocks until completion."""
    cfg = cfg or CodexConfig()
    _ensure_skill_path()
    _check_binary(cfg)
    _check_auth()
    _check_orchestrator_lock(cfg, expected_owner_pid=expected_orchestrator_pid)
    skill_path(skill_id)  # raises if missing
    ensure_lab_runs_dir()

    profile = get_profile(skill_id, override=profile_override)
    schema_path = _resolve_output_schema(skill_id, profile)
    settings = effective_settings(skill_id, cfg, profile)

    spawn_id = _new_spawn_id()
    log_path = _log_path_for(skill_id, spawn_id)
    last_msg_path = log_path.with_suffix(".last.txt")
    prompt = _render_prompt(skill_id, args, schema_path=schema_path)

    argv = _build_argv(
        skill_id, cfg, profile,
        last_msg_path=last_msg_path,
        schema_path=schema_path,
    )

    # Canonical env hand-off so skills can attribute their work
    # accurately. The agent inside `codex exec` has no built-in way
    # to know which model / effort / summary it was launched with;
    # without this it guesses (we saw a critic write
    # `critic_model="gpt-5-codex"` while actually running on
    # gpt-5.4). Skills should prefer these env vars over hardcoding.
    env = os.environ.copy()
    # Hard guarantee: codex must use the ChatGPT subscription, never an
    # API key. Cursor (and other parent processes) sometimes inject
    # OPENAI_API_KEY into our env; if we forwarded that to the child,
    # codex would silently prefer it over the ChatGPT credentials in
    # ~/.codex/auth.json and we'd burn the wrong (much smaller) quota.
    # Strip every known shape of the variable so the child literally
    # cannot see it.
    for k in ("OPENAI_API_KEY", "OPENAI_KEY", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION"):
        env.pop(k, None)
    env["OPENHARNESS_CODEX_MODEL"] = str(settings["model"])
    env["OPENHARNESS_CODEX_EFFORT"] = str(settings["reasoning_effort"])
    env["OPENHARNESS_CODEX_SUMMARY"] = str(settings["reasoning_summary"])
    env["OPENHARNESS_LAB_SKILL"] = skill_id
    env["OPENHARNESS_LAB_SPAWN_ID"] = spawn_id
    if extra_env:
        env.update(extra_env)

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    timeout_sec = profile.timeout_sec or cfg.default_timeout_sec

    # If singleton, take the per-skill lock first, then the global pool.
    # Acquire order matters: skill lock outside the pool means a long
    # singleton run doesn't sit on a pool slot waiting for itself.
    if profile.singleton:
        skill_lock_cm: contextlib.AbstractContextManager = _singleton_lock_for(skill_id)
    else:
        skill_lock_cm = contextlib.nullcontext()

    with skill_lock_cm, cfg.semaphore:
        with log_path.open("w") as logfh:
            logfh.write(f"# spawn_id: {spawn_id}\n")
            logfh.write(f"# skill: {skill_id}\n")
            logfh.write(f"# args: {args!r}\n")
            logfh.write(f"# started_at: {started.isoformat()}\n")
            logfh.write(f"# effective_settings: {json.dumps(settings)}\n")
            logfh.write("# command: " + " ".join(argv) + "\n")
            logfh.write("# --- prompt --- #\n")
            logfh.write(prompt)
            logfh.write("\n# --- codex stdout (jsonl events) --- #\n")
            logfh.flush()

            try:
                proc = subprocess.run(
                    argv,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=str(cfg.cwd),
                    env=env,
                    timeout=timeout_sec,
                )
                logfh.write(proc.stdout or "")
                logfh.write("\n# --- codex stderr --- #\n")
                logfh.write(proc.stderr or "")
                exit_code = proc.returncode
            except subprocess.TimeoutExpired as exc:
                logfh.write(f"\n# TIMEOUT after {timeout_sec}s: {exc}\n")
                exit_code = 124  # convention: timeout

    finished = datetime.now(timezone.utc)
    duration = time.monotonic() - t0
    last_msg = None
    if last_msg_path.is_file():
        with contextlib.suppress(OSError):
            last_msg = _parse_last_message(last_msg_path.read_text())

    result = SpawnResult(
        spawn_id=spawn_id,
        skill=skill_id,
        args=list(args),
        exit_code=exit_code,
        log_path=log_path,
        last_message=last_msg,
        started_at=started,
        finished_at=finished,
        duration_sec=duration,
        effective_settings=settings,
        parent_run_dir=parent_run_dir,
    )
    if cfg.record_in_db:
        _record_spawn(result, parent_run_dir=parent_run_dir)
    return result


# ----- helpers used by the orchestrator ------------------------------------


def run_many(
    invocations: Iterable[tuple[str, Sequence[str]]],
    *,
    cfg: CodexConfig | None = None,
    profile_override: SkillProfile | None = None,
    parent_run_dir: Path | None = None,
) -> list[SpawnResult]:
    """Run a batch of (skill_id, args) tuples respecting the semaphore."""
    cfg = cfg or CodexConfig()
    results: list[SpawnResult] = []
    threads: list[threading.Thread] = []
    out_lock = threading.Lock()

    def _worker(skill_id: str, args: Sequence[str]) -> None:
        try:
            r = run(
                skill_id, args, cfg=cfg,
                profile_override=profile_override,
                parent_run_dir=parent_run_dir,
            )
        except CodexAdapterError as exc:
            logger.error("adapter error invoking %s %r: %s", skill_id, args, exc)
            return
        with out_lock:
            results.append(r)

    for skill_id, args in invocations:
        t = threading.Thread(
            target=_worker, args=(skill_id, args), name=f"codex-{skill_id}", daemon=False
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()
    return results


# ----- orchestrator lock helpers (used by runner.py) -----------------------


@contextlib.contextmanager
def orchestrator_lock(*, owner: str | None = None) -> Iterator[Path]:
    """Acquire `runs/lab/orchestrator.lock` for the duration of the block."""
    ensure_lab_runs_dir()
    if ORCHESTRATOR_LOCK_PATH.is_file():
        try:
            cur = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            cur = {}
        raise CodexAdapterError(
            f"Orchestrator lock already held: {cur!r} (at "
            f"{ORCHESTRATOR_LOCK_PATH}). Refusing to start a second daemon."
        )
    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "owner": owner or sys.argv[0],
    }
    ORCHESTRATOR_LOCK_PATH.write_text(json.dumps(payload, indent=2))
    try:
        yield ORCHESTRATOR_LOCK_PATH
    finally:
        with contextlib.suppress(OSError):
            ORCHESTRATOR_LOCK_PATH.unlink()


def force_release_lock() -> bool:
    """Remove a stale lock file. Caller is responsible for verifying staleness."""
    if ORCHESTRATOR_LOCK_PATH.is_file():
        ORCHESTRATOR_LOCK_PATH.unlink()
        return True
    return False
