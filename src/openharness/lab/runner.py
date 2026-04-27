"""Orchestrator daemon — autonomous driver of the phased lab pipeline.

Each top entry in ``lab/roadmap.md > ## Up next`` advances through a
fixed 7-phase pipeline. Each phase has its own status in
``runs/lab/state/<slug>/phases.json`` (see :mod:`phase_state`), so a
crashed daemon resumes exactly where it left off — no re-running the
design phase just because implement timed out.

Phases, in order:

    0. preflight (deterministic, this module)
        - Assert parent repo is clean, checked out on ``main``, and
          fast-forwarded to ``origin/main``; create worktree
          ``../OpenHarness.worktrees/lab-<slug>/`` on branch
          ``lab/<slug>`` rooted at that exact ``main`` SHA.
        - Idempotent: a warm worktree at the same SHA is reused.

    1. design (codex spawn `lab-design-variant`)
        - Read-only sandbox. Produces
          ``runs/lab/state/<slug>/design.md``.
        - Skipped when the entry is a baseline / infrastructure run
          (``needs_variant=False``).

    2. implement (codex spawn `lab-implement-variant`)
        - Workspace-write sandbox scoped to the worktree. Produces
          one or more git commits on ``lab/<slug>``, plus
          ``runs/lab/state/<slug>/implement.json`` recording commit
          shas, files touched, and validation outcomes.
        - Skipped when ``needs_variant=False``.

    3. run (deterministic, this module + :mod:`phase_run`)
        - Append the journal entry stub to the worktree copy of
          ``lab/experiments.md``.
        - Launch ``uv run exec`` from inside the worktree, with
          ``--root`` pinned at the parent repo's
          ``runs/experiments/<instance-id>/`` so existing lab
          tooling sees the artefacts.
        - Poll for ``results/summary.md``.

    4. critique (deterministic, this module)
        - ``uv run lab ingest`` → DuckDB.
        - Fan out ``trial-critic`` and ``task-features`` spawns.
        - Run ``experiment-critic`` once all trial critiques land.
        - ``ingest-critiques``, ``journal_synth.synthesize``, then
          load the structured ``experiment-critic`` evaluation and
          apply it to the worktree copy of ``lab/``.
        - The evaluation verdict (accept / reject / no_op) is recorded in
          ``phases.json`` so phase 5 can read it.

    5. replan (codex spawn ``lab-replan-roadmap``)
        - Deep postmortem over the finished run + verdict + current
          tree state.
        - May run ``cross-experiment-critic`` first to refresh the
          cross-run view.
        - Mutates the worktree copies of ``lab/roadmap.md`` and
          ``lab/ideas.md``: move the just-ran slug to ``## Done``,
          add / demote / remove / reprioritise roadmap entries, and
          record follow-ups.

    6. finalize (codex spawn ``lab-finalize-pr``)
        - Workspace-write sandbox. Rebase/resolve against latest
          ``main`` as needed, open the canonical experiment PR, and
          sync the experiment outcome back to ``main`` before the loop
          advances.
        - ``accept`` comments on and merges the experiment PR itself.
          ``reject`` / ``noop`` comment on and close that PR, then sync
          lab metadata separately while recording the discarded
          implementation SHA.
        - After outcome sync, this module fast-forwards the parent repo's
          ``main`` checkout and deletes the worktree.

The daemon is **single-tenant**: it acquires
``runs/lab/orchestrator.lock`` at startup and refuses to run if
another holder is still live. Stop with ``uv run lab daemon stop``
(SIGTERM the recorded pid) or remove the lock manually if known
stale.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openharness.lab import codex as codex_adapter
from openharness.lab import critic_io
from openharness.lab import daemon_state as ds
from openharness.lab import db as labdb
from openharness.lab import gemini as gemini_adapter
from openharness.lab import gcs_sync
from openharness.lab import ingest as labingest
from openharness.lab import journal_synth
from openharness.lab import lab_docs
from openharness.lab import phase_run as phase_run_mod
from openharness.lab import phase_state
from openharness.lab import preflight as preflight_mod
from openharness.lab import tree as labtree
from openharness.lab import evaluation
from openharness.lab.paths import (
    EXPERIMENTS_RUNS_ROOT,
    LAB_ROOT,
    ORCHESTRATOR_LOCK_PATH,
    REPO_ROOT,
    ensure_lab_runs_dir,
)

logger = logging.getLogger("openharness.lab.runner")

DEFAULT_POLL_INTERVAL_SEC = 60
DEFAULT_RUN_TIMEOUT_SEC = 16 * 60 * 60  # 16h cap on a single experiment run.
DEFAULT_IDLE_SLEEP_SEC = 15  # idle poll cadence; SIGUSR1 wakes early.
DEFAULT_XEXP_EVERY = 1  # cross-experiment-critic every M runs.

# Module-level wake event. The runner installs a SIGUSR1 handler that
# sets this event; the loop uses Event.wait() instead of time.sleep()
# so any state mutation from the CLI / web UI (which calls
# ``daemon_state.notify_daemon``) becomes visible within milliseconds
# instead of after the full ``idle_sleep_sec`` cadence. See
# ``_idle_wait`` and ``_install_signal_cleanup``.
_WAKE_EVENT = threading.Event()


# ----- roadmap parsing ------------------------------------------------------


@dataclass(slots=True)
class RoadmapEntry:
    slug: str
    body: str
    idea_id: str | None
    hypothesis: str
    depends_on: list[str] = field(default_factory=list)


_ROADMAP_ENTRY_RE = re.compile(r"^### (\S+)\s*\n", re.MULTILINE)
_BULLET_RE = re.compile(r"^-\s*\*\*([^:]+):\*\*\s*(.*)$", re.MULTILINE)


def parse_up_next(roadmap_path: Path = LAB_ROOT / "roadmap.md") -> list[RoadmapEntry]:
    """Return entries under `## Up next` in priority order (top first)."""
    text = roadmap_path.read_text()
    sections = re.split(r"(?m)^## ", text)
    up_next_block: str | None = None
    for sec in sections:
        if sec.startswith("Up next"):
            up_next_block = sec[len("Up next") :].strip()
            break
    if not up_next_block or up_next_block.startswith("_(none)_"):
        return []
    suggested = re.search(r"(?m)^### Suggested\s*$", up_next_block)
    if suggested:
        up_next_block = up_next_block[: suggested.start()].strip()
    if not up_next_block or up_next_block.startswith("_(none)_"):
        return []
    entries: list[RoadmapEntry] = []
    matches = list(_ROADMAP_ENTRY_RE.finditer(up_next_block))
    for i, m in enumerate(matches):
        slug = m.group(1)
        if slug == "Suggested":
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(up_next_block)
        body = up_next_block[start:end].strip()
        idea_id: str | None = None
        hypothesis = ""
        depends_on: list[str] = []
        for bm in _BULLET_RE.finditer(body):
            key, val = bm.group(1).strip(), bm.group(2).strip()
            if key == "Idea":
                m_id = re.search(r"\[`([^`]+)`\]", val)
                idea_id = m_id.group(1) if m_id else None
            elif key == "Hypothesis":
                hypothesis = val
            elif key == "Depends on":
                depends_on = re.findall(r"`([^`]+)`", val)
        entries.append(
            RoadmapEntry(
                slug=slug,
                body=body,
                idea_id=idea_id,
                hypothesis=hypothesis,
                depends_on=depends_on,
            )
        )
    return entries


def is_dependency_satisfied(
    entry: RoadmapEntry, *, roadmap_path: Path = LAB_ROOT / "roadmap.md"
) -> bool:
    """A dependency is satisfied if every depends-on slug already lives in `## Done`."""
    if not entry.depends_on:
        return True
    text = roadmap_path.read_text()
    done_block = text.split("## Done", 1)[-1] if "## Done" in text else ""
    for dep in entry.depends_on:
        if not re.search(rf"^### {re.escape(dep)}\b", done_block, re.MULTILINE):
            return False
    return True


# ----- run-dir polling -----------------------------------------------------


def find_latest_run_dir(*, since: datetime | None = None) -> Path | None:
    """Return the newest `runs/experiments/<id>/` whose mtime is after `since`."""
    if not EXPERIMENTS_RUNS_ROOT.is_dir():
        return None
    candidates: list[Path] = []
    for d in EXPERIMENTS_RUNS_ROOT.iterdir():
        if not d.is_dir():
            continue
        if since is not None and datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc) < since:
            continue
        candidates.append(d)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def wait_for_summary(
    run_dir: Path, *, timeout: int = DEFAULT_RUN_TIMEOUT_SEC, poll: int = DEFAULT_POLL_INTERVAL_SEC
) -> bool:
    """Block until `results/summary.md` appears or timeout expires."""
    deadline = time.monotonic() + timeout
    summary = run_dir / "results" / "summary.md"
    while time.monotonic() < deadline:
        if summary.is_file():
            return True
        time.sleep(poll)
    return summary.is_file()


def _tail_log_for_summary(log_path: Path, *, max_chars: int = 200) -> str:
    """Extract the most useful last line of a codex spawn log for UI display.

    Codex spawn logs end with two clearly-labelled blocks:

        # --- codex stdout (jsonl events) --- #
        <stream of jsonl events or empty if codex never started>
        # --- codex stderr --- #
        <whatever the binary printed; e.g. "node: command not found">

    For the "no output, exit non-zero" failure mode (most often a PATH
    issue or a missing binary), the *only* diagnostic lives in that
    stderr block. We pick the last non-empty line and truncate it so
    it fits in a single history-row caption.

    Returns ``""`` when the log can't be read or contains nothing
    useful — the caller falls back to a generic "see log" hint.
    """
    if not log_path or not log_path.is_file():
        return ""
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return ""
    # Take everything after the stderr marker; if absent, take the last
    # 2KB so we still surface SOMETHING (e.g. a Python traceback).
    marker = "# --- codex stderr --- #"
    chunk = text.rsplit(marker, 1)[-1] if marker in text else text[-2048:]
    last_meaningful = ""
    for line in reversed(chunk.splitlines()):
        s = line.strip()
        if s:
            last_meaningful = s
            break
    if not last_meaningful:
        return ""
    if len(last_meaningful) > max_chars:
        last_meaningful = last_meaningful[: max_chars - 1] + "…"
    return last_meaningful


# ----- inner loop ----------------------------------------------------------


@dataclass(slots=True)
class OrchestratorConfig:
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC
    run_timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC
    idle_sleep_sec: int = DEFAULT_IDLE_SLEEP_SEC
    xexp_every: int = DEFAULT_XEXP_EVERY
    max_concurrency: int = codex_adapter.DEFAULT_MAX_CONCURRENCY
    once: bool = False
    dry_run: bool = False
    codex_cfg: codex_adapter.CodexConfig | None = None


def _codex_cfg(cfg: OrchestratorConfig) -> codex_adapter.CodexConfig:
    if cfg.codex_cfg is not None:
        return cfg.codex_cfg
    return codex_adapter.CodexConfig(
        max_concurrency=cfg.max_concurrency,
        enforce_orchestrator_lock=True,
        record_in_db=True,
    )


def _worktree_cx(
    cfg: OrchestratorConfig,
    *,
    worktree: Path,
) -> codex_adapter.CodexConfig:
    cx = _codex_cfg(cfg)
    cx.cwd = worktree
    return cx


def _worktree_lab_root(worktree: Path) -> Path:
    return worktree / "lab"


def trials_needing_critique(instance_id: str) -> list[tuple[str, str]]:
    """Trials in `instance_id` lacking `critic/trial-critic.json` on disk.

    The trials registry lives in DuckDB (cheap iteration) but the
    presence test is purely on-disk: a trial is "done" when the
    critic file exists next to its evidence. This avoids having to
    keep the DB cache up to date during a backfill.

    Returns `[(trial_id, trial_dir), ...]` ordered by task and leg
    so logs read predictably during a backfill.
    """
    with labdb.reader() as conn:
        rows = conn.execute(
            "SELECT trial_id, trial_dir FROM trials WHERE instance_id = ? "
            "ORDER BY task_name, leg_id",
            [instance_id],
        ).fetchall()
    out: list[tuple[str, str]] = []
    for trial_id, trial_dir in rows:
        if not critic_io.trial_critique_path(trial_dir).is_file():
            out.append((trial_id, trial_dir))
    return out


def checksums_needing_features(instance_id: str) -> list[str]:
    """`task_checksum`s touched by `instance_id` lacking a feature file.

    Features are keyed by checksum (deduped across experiments), so a
    backfill of one experiment may discover checksums that were ALSO
    touched by other experiments and never extracted. We still scope
    by `instance_id` here because the orchestrator only ever asks
    "what does THIS run need"; a global pass uses
    `lab analyze --include-cross-experiment` plus its own walk.
    """
    with labdb.reader() as conn:
        rows = conn.execute(
            "SELECT DISTINCT task_checksum FROM trials "
            "WHERE instance_id = ? AND task_checksum IS NOT NULL",
            [instance_id],
        ).fetchall()
    out: list[str] = []
    for (checksum,) in rows:
        if not critic_io.task_features_path(checksum).is_file():
            out.append(checksum)
    return out


def comparison_exists(instance_id: str) -> bool:
    """True iff `<run_dir>/critic/comparisons/` has at least one file."""
    run_dir = critic_io.run_dir_from_instance(instance_id)
    if run_dir is None:
        return False
    cmp_dir = run_dir / critic_io.CRITIC_DIRNAME / "comparisons"
    if not cmp_dir.is_dir():
        return False
    return any(cmp_dir.glob("*.json"))


def instance_exists(instance_id: str) -> bool:
    with labdb.reader() as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM experiments WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
    return int(n) > 0


def _completed_runs_count() -> int:
    with labdb.reader() as conn:
        (n,) = conn.execute("SELECT count(*) FROM experiments").fetchone()
    return int(n)


@dataclass(slots=True)
class TickResult:
    """Outcome of one full ``_process_entry`` call.

    ``loop`` consults ``outcome`` to decide what to record in
    history and whether to fire the failure gate.
    """

    ok: bool
    outcome: ds.TickOutcome
    summary: str | None = None


# ---------------------------------------------------------------------------
# Phased pipeline — one helper per phase, plus the orchestrator entry point.
#
# Each ``_phase_*`` helper:
#   - Receives the slug, the OrchestratorConfig, the loaded
#     SlugPhases (so it can read prior payloads), and any phase-
#     specific context.
#   - Marks the phase ``running`` on entry, ``ok`` / ``failed`` /
#     ``skipped`` on exit, with the right payload.
#   - Returns a (TickPhase, TickOutcome, summary) tuple on failure
#     so ``_process_entry_phased`` can short-circuit.
#   - Returns ``None`` on success, letting the caller advance.
# ---------------------------------------------------------------------------


# Roadmap entries whose work should bypass design+implement entirely.
# Heuristic: idea_id is one of these magic strings, OR the slug ends
# in a hand-curated infrastructure/baseline marker.
_BASELINE_IDEA_IDS: frozenset[str] = frozenset(
    {
        "baseline snapshot",
        "infrastructure",
        "baseline",
        "infra",
    }
)


def _entry_needs_variant(entry: RoadmapEntry) -> bool:
    """True iff this entry should go through design + implement phases.

    Baseline / infrastructure entries reuse a checked-in spec
    verbatim and have no variant code to write. They go straight from
    preflight (which still creates a worktree for isolation) to run.
    """
    if entry.idea_id and entry.idea_id.lower() in _BASELINE_IDEA_IDS:
        return False
    # Heuristic fallback: the convention is ``<topic>-baseline-<scope>``
    # for re-baselining sweeps. Conservative: only match -baseline-.
    return "-baseline-" not in entry.slug


def _summary_truncate(text: str, *, n: int = 200) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Auto-repair: turn a failed phase's prior errors into a small markdown
# file the next spawn can read via ``--repair-context=<path>``.
#
# Why a file (and not just CLI arg-stuffing)?
#  - Codex spawns log every CLI arg verbatim into the prompt header
#    AND the audit log. A multi-paragraph repair block bloats the
#    log and makes the prompt brittle to shell-escaping bugs.
#  - The skill's SKILL.md can reference the file by name, document
#    the format once, and treat it as just another input.
#  - The artifact survives in ``runs/lab/state/<slug>/`` so an
#    operator can read EXACTLY what context the repair attempt got.
# ---------------------------------------------------------------------------


def _repair_context_path(slug: str, phase: phase_state.PhaseName) -> Path:
    """Where ``_write_repair_context`` writes / where the spawn reads from."""
    return phase_state.slug_dir(slug) / f"repair-{phase}.md"


def _format_repair_context(
    *,
    slug: str,
    phase: phase_state.PhaseName,
    prior_failures: list[str],
    attempt_number: int,
    max_attempts: int,
) -> str:
    """Render the repair-context markdown the skill will read.

    Kept intentionally short: the model reads a handful of these per
    spawn, and verbose context dilutes the actual failure signal.
    Order = newest first so the most relevant message lands at the
    top of the model's attention budget.
    """
    lines: list[str] = [
        f"# Repair context — `{slug}` / `{phase}`",
        "",
        f"This is **repair attempt {attempt_number} of {max_attempts}** "
        "for this phase. The previous attempt(s) failed with the messages "
        "below (newest first).",
        "",
        "Your job, in order:",
        "",
        "1. Read the failure(s) and identify the root cause.",
        "2. If the failure is *your* fault (a real bug in the artefact "
        "you produced), fix it and proceed normally.",
        "3. If the failure is a **contract ambiguity** — the design / "
        "roadmap asks for something that cannot be satisfied as "
        "literally specified — write a `design_amendment.md` (next "
        "to `design.md` in the same directory) explaining the "
        "minimal amendment, then proceed using the amended contract. "
        "The amendment MUST preserve the hypothesis and the axis "
        "count; it may relax exact slice sizes, swap a near-miss "
        "predicate for the available equivalent, etc.",
        "4. If the failure indicates the idea is genuinely unbuildable "
        "with current code, REFUSE again with a one-paragraph "
        "blocker that names exactly what is missing — the "
        "orchestrator will then exhaust the repair budget and "
        "block the slug, which is the right outcome.",
        "",
        "Do **not** repeat the previous failure's mistake. If you "
        "REFUSE a second time for the same reason, the orchestrator "
        "treats that as terminal.",
        "",
    ]
    for i, msg in enumerate(reversed(prior_failures), start=1):
        lines.append(f"## Prior failure {i}")
        lines.append("")
        lines.append("```")
        lines.append(msg.strip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _maybe_write_repair_context(
    slug: str,
    phase: phase_state.PhaseName,
    state: phase_state.SlugPhases,
) -> tuple[Path, int] | None:
    """Materialise the repair-context file iff the phase has prior failures.

    Returns ``(path, attempt_number)`` for the caller to splice into
    the spawn's CLI args, or ``None`` if this is a clean first
    attempt (no prior failures).
    """
    rec = state.get(phase)
    if not rec.prior_failures:
        return None
    path = _repair_context_path(slug, phase)
    attempt_number = rec.failure_count + 1  # 1-indexed for humans
    text = _format_repair_context(
        slug=slug,
        phase=phase,
        prior_failures=rec.prior_failures,
        attempt_number=attempt_number,
        max_attempts=phase_state.MAX_REPAIRS_PER_PHASE + 1,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, attempt_number


def _repair_args(
    slug: str, phase: phase_state.PhaseName, state: phase_state.SlugPhases
) -> list[str]:
    """Return the CLI flags that inject repair-context into a skill spawn.

    Empty list when the phase has no prior failures (the common
    fast-path on the first attempt). Otherwise two flags:

        --repair-context=<path>     # written by _maybe_write_repair_context
        --repair-attempt=<N>        # 1-indexed, capped at MAX_REPAIRS+1

    The skill's SKILL.md treats these as optional inputs: if absent,
    behave normally; if present, prepend the repair workflow.
    """
    info = _maybe_write_repair_context(slug, phase, state)
    if info is None:
        return []
    path, attempt_number = info
    return [
        f"--repair-context={path}",
        f"--repair-attempt={attempt_number}",
    ]


def _timed_out_run_has_summary(rec: phase_state.PhaseRecord) -> bool:
    """True when a detached run completed after the daemon's poll timeout.

    Phase 3 launches Harbor in a detached process so stopping or timing
    out the daemon does not kill an expensive experiment. If the summary
    lands after the phase has already been marked failed, the next tick
    should resume the run handler and validate the completed artifacts
    instead of being stopped by the generic repair-budget gate.
    """
    raw_run_dir = rec.payload.get("run_dir")
    if not raw_run_dir:
        return False
    run_dir = Path(str(raw_run_dir))
    return (run_dir / "results" / "summary.md").is_file()


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _commit_worktree_changes(
    *,
    worktree: Path,
    slug: str,
    phase: str,
    summary: str,
    paths: tuple[str, ...],
) -> str | None:
    """Commit tracked lab/tree changes inside the experiment worktree.

    The refactored loop keeps all durable experiment state on the
    experiment branch until finalize syncs it back to ``main``.
    Returning the commit SHA lets finalize cherry-pick lab-only
    history for ``reject`` / ``noop`` verdicts.
    """
    env = _git_env()
    diff = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=str(worktree),
        env=env,
        text=True,
        capture_output=True,
    )
    if not diff.stdout.strip():
        return None
    msg = f"lab({slug}): {phase} — {_summary_truncate(summary, n=72)}"
    subprocess.run(
        ["git", "add", "--", *paths],
        cwd=str(worktree),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(worktree),
        env=env,
        check=True,
        capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(worktree),
        env=env,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    logger.info(
        "committed worktree changes for %s phase=%s sha=%s",
        slug,
        phase,
        sha[:8],
    )
    return sha


def _worktree_changed_paths(worktree: Path) -> list[str]:
    """Return non-ignored dirty paths from `git status --porcelain`.

    The path is normalized for rename rows (`old -> new`) because callers
    only need to decide whether the resulting path is inside an allowed
    write zone.
    """
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(worktree),
        env=_git_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    out: list[str] = []
    for raw in proc.stdout.splitlines():
        if not raw:
            continue
        path = raw[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            out.append(path)
    return out


def _unexpected_worktree_paths(
    worktree: Path,
    *,
    allowed_prefixes: tuple[str, ...],
) -> list[str]:
    normalized = tuple(p.rstrip("/") + "/" for p in allowed_prefixes)
    unexpected: list[str] = []
    for path in _worktree_changed_paths(worktree):
        if any(path.startswith(prefix) for prefix in normalized):
            continue
        unexpected.append(path)
    return unexpected


def _discard_uncommitted_paths(worktree: Path, paths: list[str]) -> None:
    """Drop out-of-contract uncommitted changes left by a phase skill."""
    if not paths:
        return
    env = _git_env()
    subprocess.run(
        ["git", "restore", "--", *paths],
        cwd=str(worktree),
        env=env,
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clean", "-f", "--", *paths],
        cwd=str(worktree),
        env=env,
        check=False,
        capture_output=True,
    )


def _append_commit(payload: dict[str, object], sha: str | None) -> None:
    if not sha:
        return
    commits = payload.setdefault("lab_commits", [])
    if not isinstance(commits, list):
        commits = []
        payload["lab_commits"] = commits
    if sha not in commits:
        commits.append(sha)


def _collect_lab_commits(
    state: phase_state.SlugPhases,
    *,
    phases: tuple[str, ...] = ("run", "critique", "replan"),
) -> list[str]:
    ordered: list[str] = []
    for phase_name in phases:
        payload = state.get(phase_name).payload
        for sha in payload.get("lab_commits") or []:
            s = str(sha).strip()
            if s and s not in ordered:
                ordered.append(s)
    return ordered


def _fast_forward_parent_main() -> None:
    """Refresh the parent repo after finalize synced the outcome."""
    env = _git_env()
    subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "merge", "--ff-only", "origin/main"],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
        capture_output=True,
    )


def _archive_unmerged_finalize_json(slug: str, finalize_path: Path) -> Path:
    """Move a stale failed finalize contract aside before a retry.

    ``finalize.json`` is reusable only when it records ``merged: true``.
    A previous failed finalize may have written ``merged: false``; keeping
    that file in place makes the next tick reuse the failure instead of
    spending its repair attempt on the merge.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = finalize_path.with_name(f"finalize.unmerged-{stamp}.json")
    suffix = 1
    while archive_path.exists():
        archive_path = finalize_path.with_name(
            f"finalize.unmerged-{stamp}.{suffix}.json",
        )
        suffix += 1
    finalize_path.replace(archive_path)
    logger.warning(
        "archived unmerged finalize.json for %s at %s before retry",
        slug,
        archive_path,
    )
    return archive_path


def _load_reusable_finalize_json(slug: str, finalize_path: Path) -> dict[str, object] | None:
    """Return existing finalize data only if it already completed the merge."""
    if not finalize_path.is_file():
        return None
    finalize_data = json.loads(finalize_path.read_text())
    if finalize_data.get("merged"):
        logger.info("successful finalize.json already present for %s; reusing", slug)
        return finalize_data
    _archive_unmerged_finalize_json(slug, finalize_path)
    return None


# ----- phase 0: preflight ---------------------------------------------------


def _phase_preflight(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Create / reuse the worktree for ``entry``.

    Records ``{worktree, branch, base_sha, base_branch}`` into the
    preflight payload so every later phase can read them.
    """
    ds.update_tick(phase="preflight", note="main sync + worktree create")
    phase_state.mark_running(entry.slug, "preflight")
    try:
        result = preflight_mod.run_preflight(
            entry.slug,
            base_branch=preflight_mod.DEFAULT_BASE_BRANCH,
        )
    except preflight_mod.PreflightError as exc:
        msg = f"preflight: {exc}"
        logger.error("preflight failed for %s: %s", entry.slug, exc)
        phase_state.mark_failed(entry.slug, "preflight", error=str(exc))
        return TickResult(ok=False, outcome="error", summary=msg)
    payload = {
        "worktree": str(result.info.path),
        "branch": result.info.branch,
        "base_sha": result.base_sha,
        "base_branch": result.base_branch,
    }
    phase_state.mark_ok(entry.slug, "preflight", payload=payload)
    ds.update_tick(
        phase="preflight",
        worktree_path=str(result.info.path),
        note=f"worktree {result.info.path.name} on {result.info.branch}",
    )
    logger.info(
        "preflight ok for %s: worktree=%s branch=%s base=%s",
        entry.slug,
        result.info.path,
        result.info.branch,
        result.base_sha[:8],
    )
    return None


# ----- phase 1: design -----------------------------------------------------


def _phase_design(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Spawn ``lab-design-variant`` to produce ``design.md``."""
    if not state.needs_variant:
        phase_state.mark_skipped(
            entry.slug,
            "design",
            reason="baseline / infrastructure entry; no variant to design",
        )
        return None

    pre = state.get("preflight").payload
    worktree = Path(pre.get("worktree", ""))
    cx = _worktree_cx(cfg, worktree=worktree)
    design_path = phase_state.slug_dir(entry.slug) / "design.md"
    design_path.parent.mkdir(parents=True, exist_ok=True)

    ds.update_tick(phase="design", note="lab-design-variant")
    phase_state.mark_running(entry.slug, "design")
    repair_args = _repair_args(entry.slug, "design", state)
    res = codex_adapter.run(
        "lab-design-variant",
        [
            entry.slug,
            f"--idea={entry.idea_id or 'baseline'}",
            f"--worktree={worktree}",
            f"--design-path={design_path}",
            f"--hypothesis={entry.hypothesis}",
            # Pass the full roadmap entry body so the agent has the
            # Plan + Cost + Depends-on context without needing to re-read
            # the markdown file (the read-only sandbox can still read it;
            # this is a convenience for clarity in the agent's context).
            f"--roadmap-body={entry.body}",
            *repair_args,
        ],
        cfg=cx,
        expected_orchestrator_pid=os.getpid(),
    )
    last = (res.last_message or "").strip()
    if last.upper().startswith("REFUSE"):
        msg = f"design refused: {_summary_truncate(last, n=160)}"
        phase_state.mark_failed(entry.slug, "design", error=last[:500])
        return TickResult(ok=False, outcome="refuse", summary=msg)
    if not res.ok:
        tail = _tail_log_for_summary(res.log_path) if not last else ""
        body = last[:160] or tail or f"(see log {res.log_path.name})"
        msg = f"design spawn exit={res.exit_code}: {body}"
        phase_state.mark_failed(
            entry.slug,
            "design",
            error=msg,
            payload={"log_path": str(res.log_path) if res.log_path else None},
        )
        return TickResult(ok=False, outcome="error", summary=msg)
    if not design_path.is_file():
        msg = f"design spawn ok but {design_path.name} missing"
        phase_state.mark_failed(entry.slug, "design", error=msg)
        return TickResult(ok=False, outcome="no-run-dir", summary=msg)
    phase_state.mark_ok(
        entry.slug,
        "design",
        payload={
            "design_path": str(design_path),
            "log_path": str(res.log_path) if res.log_path else None,
        },
    )
    return None


# ----- phase 2: implement --------------------------------------------------


def _phase_implement(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Spawn ``lab-implement-variant`` to apply the design in the worktree."""
    if not state.needs_variant:
        phase_state.mark_skipped(
            entry.slug,
            "implement",
            reason="baseline / infrastructure entry; nothing to implement",
        )
        return None

    pre = state.get("preflight").payload
    worktree = Path(pre.get("worktree", ""))
    cx = _worktree_cx(cfg, worktree=worktree)
    design_path = state.get("design").payload.get("design_path")
    implement_path = phase_state.slug_dir(entry.slug) / "implement.json"

    ds.update_tick(phase="implement", note="lab-implement-variant")
    phase_state.mark_running(entry.slug, "implement")
    repair_args = _repair_args(entry.slug, "implement", state)
    res = codex_adapter.run(
        "lab-implement-variant",
        [
            entry.slug,
            f"--worktree={worktree}",
            f"--design-path={design_path or ''}",
            f"--implement-json={implement_path}",
            f"--base-sha={pre.get('base_sha', '')}",
            *repair_args,
        ],
        cfg=cx,
        expected_orchestrator_pid=os.getpid(),
    )
    last = (res.last_message or "").strip()
    if last.upper().startswith("REFUSE"):
        msg = f"implement refused: {_summary_truncate(last, n=160)}"
        phase_state.mark_failed(entry.slug, "implement", error=last[:500])
        return TickResult(ok=False, outcome="refuse", summary=msg)
    if not res.ok:
        tail = _tail_log_for_summary(res.log_path) if not last else ""
        body = last[:160] or tail or f"(see log {res.log_path.name})"
        msg = f"implement spawn exit={res.exit_code}: {body}"
        phase_state.mark_failed(entry.slug, "implement", error=msg)
        return TickResult(ok=False, outcome="error", summary=msg)
    if not implement_path.is_file():
        msg = f"implement spawn ok but {implement_path.name} missing"
        phase_state.mark_failed(entry.slug, "implement", error=msg)
        return TickResult(ok=False, outcome="no-run-dir", summary=msg)

    try:
        implement_data = json.loads(implement_path.read_text())
    except json.JSONDecodeError as exc:
        msg = f"implement.json malformed: {exc}"
        phase_state.mark_failed(entry.slug, "implement", error=msg)
        return TickResult(ok=False, outcome="error", summary=msg)

    validations = implement_data.get("validations", {})
    failed_checks = [k for k, v in validations.items() if str(v).startswith("failed")]
    if failed_checks:
        msg = f"implement validations failed: {failed_checks}"
        phase_state.mark_failed(
            entry.slug,
            "implement",
            error=msg,
            payload=implement_data,
        )
        return TickResult(ok=False, outcome="error", summary=msg)

    # Smoke validation gate. The implement skill must run a smoke
    # exec (e.g. `uv run exec <spec> --profile smoke`) and report
    # the outcome here. Missing smoke block, errors, or any leg in
    # ERRORED state blocks the (much more expensive) full run.
    smoke = implement_data.get("smoke")
    if not isinstance(smoke, dict):
        msg = (
            "implement.json missing required `smoke` block "
            "(implement phase MUST run `uv run exec <spec> --profile smoke` "
            "and record the outcome before phase 3 can run)"
        )
        phase_state.mark_failed(
            entry.slug,
            "implement",
            error=msg,
            payload=implement_data,
        )
        return TickResult(ok=False, outcome="error", summary=msg)
    smoke_errors = smoke.get("errors") or []
    if smoke_errors:
        msg = (
            f"implement smoke run reported {len(smoke_errors)} error(s): "
            f"{_summary_truncate(str(smoke_errors), n=200)}"
        )
        phase_state.mark_failed(
            entry.slug,
            "implement",
            error=msg,
            payload=implement_data,
        )
        return TickResult(ok=False, outcome="error", summary=msg)
    legs = smoke.get("legs") or []
    if not legs:
        msg = "implement smoke block has no legs[] — wiring almost certainly broken"
        phase_state.mark_failed(
            entry.slug,
            "implement",
            error=msg,
            payload=implement_data,
        )
        return TickResult(ok=False, outcome="error", summary=msg)
    errored_legs = [
        leg.get("leg_id") or "(?)" for leg in legs if isinstance(leg, dict) and leg.get("errored")
    ]
    if errored_legs:
        msg = f"implement smoke ERRORED on legs: {errored_legs}"
        phase_state.mark_failed(
            entry.slug,
            "implement",
            error=msg,
            payload=implement_data,
        )
        return TickResult(ok=False, outcome="error", summary=msg)
    no_trial_legs = [
        leg.get("leg_id") or "(?)"
        for leg in legs
        if isinstance(leg, dict) and not int(leg.get("trials_run") or 0) > 0
    ]
    if no_trial_legs:
        msg = (
            f"implement smoke ran zero trials on legs: {no_trial_legs} — "
            "smoke must complete at least one trial per leg"
        )
        phase_state.mark_failed(
            entry.slug,
            "implement",
            error=msg,
            payload=implement_data,
        )
        return TickResult(ok=False, outcome="error", summary=msg)

    phase_state.mark_ok(entry.slug, "implement", payload=implement_data)
    return None


# ----- phase 3: run --------------------------------------------------------


def _phase_run(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Append the journal stub and launch + poll the experiment."""
    pre = state.get("preflight").payload
    impl = state.get("implement").payload if state.needs_variant else {}
    spec_name = impl.get("spec_name") or entry.slug
    profile = impl.get("profile")
    worktree = Path(pre["worktree"])
    lab_root = _worktree_lab_root(worktree)
    run_payload: dict[str, object] = {}

    # Determine the operational baseline for the journal header. We re-resolve
    # it at run-time rather than design-time so accepted experiments
    # that landed while this entry was queued are reflected.
    snap = lab_docs.tree_snapshot()
    operational_baseline_id = snap.operational_baseline_id or "unknown"

    # Decide journal entry type. Baselines are broad-sweep; everything
    # else defaults to paired-ablation. The implement payload may
    # override this if needed (e.g. a smoke variant).
    if not state.needs_variant:
        journal_type = "broad-sweep"
        mutation = None
    else:
        journal_type = impl.get("journal_type", "paired-ablation")
        mutation = impl.get("mutation_summary") or None

    # Append the stub (idempotent — no-op on resume after partial crash).
    try:
        phase_run_mod.append_journal_stub(
            slug=entry.slug,
            type_=journal_type,
            operational_baseline_id=operational_baseline_id,
            mutation=mutation,
            hypothesis=entry.hypothesis,
            branch=pre["branch"],
            repo_root=worktree,
        )
    except lab_docs.LabDocError as exc:
        msg = f"journal stub failed: {exc}"
        phase_state.mark_failed(entry.slug, "run", error=msg)
        return TickResult(ok=False, outcome="error", summary=msg)

    _append_commit(
        run_payload,
        _commit_worktree_changes(
            worktree=worktree,
            slug=entry.slug,
            phase="run-start",
            summary=f"append journal entry for {entry.slug}",
            paths=("lab/",),
        ),
    )

    ds.update_tick(
        phase="run",
        note=f"launching exec {spec_name} in {worktree.name}",
    )
    resume_id = state.get("run").payload.get("instance_id")
    phase_state.mark_running(entry.slug, "run")
    if run_payload:
        phase_state.update_payload(entry.slug, "run", run_payload)

    def _record_launched_run(outcome: phase_run_mod.RunOutcome) -> None:
        phase_state.update_payload(
            entry.slug,
            "run",
            {
                **run_payload,
                "instance_id": outcome.instance_id,
                "run_dir": str(outcome.run_dir),
                "spec_name": outcome.spec_name,
                "log_path": str(outcome.log_path),
                "baseline_at_runtime": operational_baseline_id,
            },
        )

    try:
        outcome = phase_run_mod.run_experiment(
            slug=entry.slug,
            worktree=worktree,
            spec_name=spec_name,
            profile=profile,
            timeout_sec=cfg.run_timeout_sec,
            poll_interval_sec=cfg.poll_interval_sec,
            resume_instance_id=resume_id,
            on_launch=_record_launched_run if not resume_id else None,
        )
    except phase_run_mod.PhaseRunError as exc:
        msg = str(exc)
        logger.error("run phase failed for %s: %s", entry.slug, exc)
        phase_state.mark_failed(entry.slug, "run", error=msg)
        return TickResult(ok=False, outcome="timeout", summary=_summary_truncate(msg))

    # Now we know the instance id — patch the Run: bullet in the journal.
    try:
        lab_docs.set_journal_run_path(
            slug=entry.slug,
            instance_id=outcome.instance_id,
            lab_root=lab_root,
        )
        _append_commit(
            run_payload,
            _commit_worktree_changes(
                worktree=worktree,
                slug=entry.slug,
                phase="run-done",
                summary=f"link runs/experiments/{outcome.instance_id}",
                paths=("lab/",),
            ),
        )
    except lab_docs.LabDocError as exc:
        # Non-fatal — the entry already exists; the Run bullet just
        # didn't get patched. Log loudly so the operator can fix.
        logger.warning(
            "could not update journal Run bullet for %s: %s",
            entry.slug,
            exc,
        )

    run_payload.update(
        {
            "instance_id": outcome.instance_id,
            "run_dir": str(outcome.run_dir),
            "spec_name": outcome.spec_name,
            "log_path": str(outcome.log_path),
            "baseline_at_runtime": operational_baseline_id,
        }
    )
    phase_state.mark_ok(entry.slug, "run", payload=run_payload)
    gcs_sync.maybe_auto_push(
        instance_id=outcome.instance_id,
        include_lab_wide=False,
    )
    return None


# ----- phase 4: critique ---------------------------------------------------


def _phase_critique(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Ingest, fan-out critic spawns, synthesize, and apply tree diff."""
    pre = state.get("preflight").payload
    worktree = Path(pre["worktree"])
    lab_root = _worktree_lab_root(worktree)
    cx = _worktree_cx(cfg, worktree=worktree)
    run_payload = state.get("run").payload
    instance_id = run_payload["instance_id"]
    run_dir = Path(run_payload["run_dir"])
    critique_payload: dict[str, object] = {}

    ds.update_tick(phase="critique", note=f"ingest {instance_id}")
    phase_state.mark_running(entry.slug, "critique")

    try:
        summary = labingest.ingest_run(run_dir)
        logger.info(
            "ingested instance=%s legs=%d trials=%d",
            summary.instance_id,
            summary.legs_inserted,
            summary.trials_inserted,
        )
    except Exception as exc:
        msg = f"ingest failed: {exc}"
        logger.exception("ingest failed for %s", entry.slug)
        phase_state.mark_failed(entry.slug, "critique", error=msg)
        return TickResult(ok=False, outcome="error", summary=msg)

    # Per-trial critic fan-out.
    needing = trials_needing_critique(summary.instance_id)
    my_pid = os.getpid()

    if needing:
        ds.update_tick(
            phase="critique",
            note=f"gemini trial-critic × {len(needing)}",
        )
        gemini_cfg = gemini_adapter.GeminiConfig(
            cwd=worktree,
            max_concurrency=cfg.max_concurrency,
        )
        try:
            results = gemini_adapter.run_many_trial_critics(
                [trial_dir for _, trial_dir in needing],
                cfg=gemini_cfg,
                parent_run_dir=run_dir,
            )
        except Exception as exc:
            msg = f"Gemini trial-critic adapter failed: {exc}"
            phase_state.mark_failed(entry.slug, "critique", error=msg)
            return TickResult(ok=False, outcome="error", summary=msg)
        failed = [r for r in results if not r.ok]
        if failed:
            msg = (
                f"{len(failed)} Gemini trial-critic spawn(s) failed; first log={failed[0].log_path}"
            )
            phase_state.mark_failed(entry.slug, "critique", error=msg)
            return TickResult(ok=False, outcome="error", summary=msg)

    unseen = checksums_needing_features(summary.instance_id)
    if unseen:
        ds.update_tick(
            phase="critique",
            note=f"task-features × {len(unseen)}",
        )
        codex_adapter.run_many(
            [("task-features", [c]) for c in unseen],
            cfg=cx,
            parent_run_dir=run_dir,
            expected_orchestrator_pid=my_pid,
        )

    still_needing = trials_needing_critique(summary.instance_id)
    if still_needing:
        msg = f"{len(still_needing)} trials still missing critiques; not running experiment-critic"
        logger.warning(msg)
        phase_state.mark_failed(entry.slug, "critique", error=msg)
        return TickResult(ok=False, outcome="error", summary=msg)
    else:
        ds.update_tick(phase="critique", note="experiment-critic")
        codex_adapter.run(
            "experiment-critic",
            [summary.instance_id],
            cfg=cx,
            parent_run_dir=run_dir,
            expected_orchestrator_pid=my_pid,
        )

    cache_counts = labingest.ingest_critiques([run_dir])
    logger.info(
        "ingest-critiques after %s: %s",
        entry.slug,
        ", ".join(f"{k}={v}" for k, v in cache_counts.items() if v),
    )

    # Journal narrative + experiment-critic evaluation.
    try:
        sections = journal_synth.synthesize(
            slug=entry.slug,
            instance_id=summary.instance_id,
            lab_root=lab_root,
        )
        logger.info("synthesize wrote %d section(s)", len(sections))
    except Exception:
        logger.exception("journal synthesize failed for %s", entry.slug)

    evaluation_result: evaluation.ExperimentEvaluation | None = None
    verdict_kind = "unknown"
    verdict_target: str | None = None
    verdict_branch_applied = False
    try:
        evaluation_result = evaluation.load_evaluation(summary.instance_id, run_dir=run_dir)
        result = labtree.apply_evaluation(
            slug=entry.slug,
            evaluation=evaluation_result,
            applied_by="auto:critique",
            lab_root=lab_root,
            mark_applied=False,
        )
        verdict_kind = evaluation_result.verdict
        verdict_target = evaluation_result.target_id
        verdict_branch_applied = result.applied
        logger.info(
            "evaluation apply %s: verdict=%s branch_applied=%s target=%s",
            entry.slug,
            evaluation_result.verdict,
            result.applied,
            evaluation_result.target_id,
        )
    except Exception:
        logger.exception("evaluation apply failed for %s", entry.slug)

    _append_commit(
        critique_payload,
        _commit_worktree_changes(
            worktree=worktree,
            slug=entry.slug,
            phase="critique",
            summary=f"verdict={verdict_kind} branch_applied={verdict_branch_applied}",
            paths=("lab/",),
        ),
    )

    critique_payload.update(
        {
            "instance_id": summary.instance_id,
            "verdict_kind": verdict_kind,
            "verdict_target": verdict_target,
            "verdict_branch_applied": verdict_branch_applied,
            "verdict_rationale": evaluation_result.rationale if evaluation_result else None,
            "verdict_confidence": evaluation_result.confidence if evaluation_result else None,
            "promotability_notes": (
                evaluation_result.promotability_notes if evaluation_result else None
            ),
            "cluster_evidence": evaluation_result.cluster_evidence if evaluation_result else [],
        }
    )
    phase_state.mark_ok(entry.slug, "critique", payload=critique_payload)
    gcs_sync.maybe_auto_push(
        instance_id=summary.instance_id,
        include_lab_wide=True,
    )
    return None


# ----- phase 5: replan -----------------------------------------------------


def _phase_replan(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Deep postmortem + roadmap mutation on the experiment branch."""
    pre = state.get("preflight").payload
    worktree = Path(pre["worktree"])
    run_payload = state.get("run").payload
    critique_payload = state.get("critique").payload
    run_dir = Path(run_payload["run_dir"])
    instance_id = str(run_payload["instance_id"])
    cx = _worktree_cx(cfg, worktree=worktree)
    replan_json = phase_state.slug_dir(entry.slug) / "replan.json"
    replan_payload: dict[str, object] = {}

    ds.update_tick(phase="replan", note="deep reflection + roadmap rewrite")
    phase_state.mark_running(entry.slug, "replan")

    if _completed_runs_count() % max(cfg.xexp_every, 1) == 0:
        try:
            codex_adapter.run(
                "cross-experiment-critic",
                [],
                cfg=cx,
                parent_run_dir=run_dir,
                expected_orchestrator_pid=os.getpid(),
            )
        except Exception:
            logger.exception("cross-experiment-critic failed during replan (non-fatal)")

    repair_args = _repair_args(entry.slug, "replan", state)
    res = codex_adapter.run(
        "lab-replan-roadmap",
        [
            entry.slug,
            f"--worktree={worktree}",
            f"--instance-id={instance_id}",
            f"--verdict={critique_payload.get('verdict_kind', 'unknown')}",
            f"--replan-json={replan_json}",
            *repair_args,
        ],
        cfg=cx,
        parent_run_dir=run_dir,
        expected_orchestrator_pid=os.getpid(),
    )
    last = (res.last_message or "").strip()
    if last.upper().startswith("REFUSE"):
        msg = f"replan refused: {_summary_truncate(last, n=160)}"
        phase_state.mark_failed(entry.slug, "replan", error=last[:500])
        return TickResult(ok=False, outcome="refuse", summary=msg)
    if not res.ok:
        tail = _tail_log_for_summary(res.log_path) if not last else ""
        body = last[:160] or tail or f"(see log {res.log_path.name})"
        msg = f"replan spawn exit={res.exit_code}: {body}"
        phase_state.mark_failed(entry.slug, "replan", error=msg)
        return TickResult(ok=False, outcome="error", summary=msg)

    if replan_json.is_file():
        try:
            raw = json.loads(replan_json.read_text())
            if isinstance(raw, dict):
                replan_payload.update(raw)
        except json.JSONDecodeError:
            logger.warning("replan.json for %s is malformed; ignoring payload", entry.slug)

    unexpected = _unexpected_worktree_paths(worktree, allowed_prefixes=("lab/",))
    if unexpected:
        _discard_uncommitted_paths(worktree, unexpected)
        msg = (
            "replan touched files outside lab/: "
            + ", ".join(unexpected[:8])
            + (" ..." if len(unexpected) > 8 else "")
        )
        phase_state.mark_failed(entry.slug, "replan", error=msg)
        return TickResult(ok=False, outcome="error", summary=_summary_truncate(msg))

    _append_commit(
        replan_payload,
        _commit_worktree_changes(
            worktree=worktree,
            slug=entry.slug,
            phase="replan",
            summary="roadmap + ideas reprioritized",
            paths=("lab/",),
        ),
    )
    phase_state.mark_ok(entry.slug, "replan", payload=replan_payload)
    return None


# ----- phase 6: finalize ---------------------------------------------------


def _phase_finalize(
    entry: RoadmapEntry,
    state: phase_state.SlugPhases,
    cfg: OrchestratorConfig,
) -> TickResult | None:
    """Finalize the canonical experiment PR and sync the outcome."""
    pre = state.get("preflight").payload
    crit = state.get("critique").payload
    run_payload = state.get("run").payload
    verdict_kind = crit.get("verdict_kind", "unknown")
    instance_id = crit.get("instance_id") or run_payload.get("instance_id")
    branch = pre.get("branch", "")
    worktree = Path(pre.get("worktree", ""))
    cx = _worktree_cx(cfg, worktree=worktree)
    lab_commits = _collect_lab_commits(state)

    ds.update_tick(phase="finalize", note=f"lab-finalize-pr verdict={verdict_kind}")
    phase_state.mark_running(entry.slug, "finalize")

    finalize_path = phase_state.slug_dir(entry.slug) / "finalize.json"
    # Resume after a crash that already produced a successful contract:
    # the operator should not get billed for another spawn. A failed
    # contract is archived and retried so the repair budget can work.
    finalize_data = _load_reusable_finalize_json(entry.slug, finalize_path)
    if finalize_data is None:
        repair_args = _repair_args(entry.slug, "finalize", state)
        res = codex_adapter.run(
            "lab-finalize-pr",
            [
                entry.slug,
                f"--worktree={worktree}",
                f"--branch={branch}",
                f"--base-branch={preflight_mod.DEFAULT_BASE_BRANCH}",
                f"--verdict={verdict_kind}",
                f"--instance-id={instance_id or ''}",
                f"--finalize-json={finalize_path}",
                *[f"--lab-commit={sha}" for sha in lab_commits],
                *repair_args,
            ],
            cfg=cx,
            expected_orchestrator_pid=os.getpid(),
        )
        last = (res.last_message or "").strip()
        if last.upper().startswith("REFUSE"):
            msg = f"finalize refused: {_summary_truncate(last, n=160)}"
            phase_state.mark_failed(entry.slug, "finalize", error=last[:500])
            return TickResult(ok=False, outcome="refuse", summary=msg)
        if not res.ok:
            tail = _tail_log_for_summary(res.log_path) if not last else ""
            body = last[:160] or tail or f"(see log {res.log_path.name})"
            msg = f"finalize spawn exit={res.exit_code}: {body}"
            phase_state.mark_failed(entry.slug, "finalize", error=msg)
            return TickResult(ok=False, outcome="error", summary=msg)
        if not finalize_path.is_file():
            # Skill claimed OK but didn't write the contract file.
            # Best-effort recovery: synthesize a minimal record so we
            # don't loop forever.
            finalize_data = {
                "cleanup_worktree": verdict_kind in ("reject", "no_op"),
                "reason": "(finalize skill returned OK without writing finalize.json)",
            }
            finalize_path.write_text(json.dumps(finalize_data, indent=2))
        else:
            finalize_data = json.loads(finalize_path.read_text())

    if not finalize_data.get("merged"):
        msg = (
            "finalize did not sync the experiment outcome back to main; "
            "refusing to advance the daemon"
        )
        phase_state.mark_failed(entry.slug, "finalize", error=msg)
        return TickResult(ok=False, outcome="error", summary=_summary_truncate(msg))

    canonical_pr_url = (
        str(finalize_data.get("experiment_pr_url") or finalize_data.get("pr_url") or "") or None
    )
    if not canonical_pr_url:
        msg = (
            "finalize did not record the canonical experiment PR URL; "
            "refusing to advance the daemon"
        )
        phase_state.mark_failed(entry.slug, "finalize", error=msg)
        return TickResult(ok=False, outcome="error", summary=_summary_truncate(msg))

    experiment_pr_state = str(finalize_data.get("experiment_pr_state") or "").lower()
    expected_pr_state = "merged" if verdict_kind == "accept" else "closed"
    if verdict_kind in ("accept", "reject", "no_op") and experiment_pr_state != expected_pr_state:
        msg = (
            f"finalize left canonical experiment PR in state "
            f"{experiment_pr_state or '(missing)'}; expected {expected_pr_state}"
        )
        phase_state.mark_failed(entry.slug, "finalize", error=msg)
        return TickResult(ok=False, outcome="error", summary=_summary_truncate(msg))

    if finalize_data.get("merged"):
        try:
            _fast_forward_parent_main()
        except Exception as exc:
            msg = f"main fast-forward failed after finalize sync: {exc}"
            phase_state.mark_failed(entry.slug, "finalize", error=msg)
            return TickResult(ok=False, outcome="error", summary=_summary_truncate(msg))

        try:
            labtree.mark_evaluation_finalized(
                instance_id=str(instance_id or ""),
                applied_by="auto:finalize",
                pr_url=canonical_pr_url,
                branch_sha=str(finalize_data.get("discarded_sha") or "") or None,
            )
        except Exception:
            logger.exception("failed to mark finalized evaluation for %s", entry.slug)

    # Worktree cleanup (deterministic, this module).
    if finalize_data.get("cleanup_worktree", True):
        try:
            preflight_mod.remove_worktree(entry.slug)
            logger.info("removed worktree for %s after %s", entry.slug, verdict_kind)
        except Exception:
            logger.exception("worktree cleanup failed for %s (non-fatal)", entry.slug)

    phase_state.mark_ok(entry.slug, "finalize", payload=finalize_data)
    ds.update_tick(
        phase="done",
        note=f"synced experiment outcome for runs/experiments/{instance_id}",
    )
    return None


# ----- the orchestrator entry point ----------------------------------------


_PHASE_DISPATCH: tuple[
    tuple[
        phase_state.PhaseName,
        # signature: (entry, state, cfg) -> TickResult | None
        "callable",  # type: ignore[name-defined]
    ],
    ...,
] = (
    ("preflight", _phase_preflight),
    ("design", _phase_design),
    ("implement", _phase_implement),
    ("run", _phase_run),
    ("critique", _phase_critique),
    ("replan", _phase_replan),
    ("finalize", _phase_finalize),
)


def _pause_after_phase_if_requested(
    *,
    entry: RoadmapEntry,
    phase_name: ds.PipelinePhase,
) -> TickResult | None:
    """Honor a one-shot operator barrier after a phase completes.

    This is intentionally checked only after the phase handler returns
    successfully. Stopping before the boundary risks killing an
    in-flight harbor run or leaving the next resume ambiguous.
    """
    if not ds.consume_pause_after_if_matches(phase=phase_name, slug=entry.slug, actor="daemon"):
        return None
    msg = f"paused after {phase_name} for {entry.slug}"
    ds.update_tick(phase=phase_name, note=msg)
    logger.info(msg)
    return TickResult(ok=True, outcome="paused", summary=msg)


def _process_entry(entry: RoadmapEntry, cfg: OrchestratorConfig) -> TickResult:
    """Run one tick of the phased pipeline for ``entry``.

    Resumes from the first non-``ok``/``skipped`` phase recorded in
    ``runs/lab/state/<entry.slug>/phases.json``. Each phase that
    runs marks itself ``running`` → ``ok`` / ``failed`` /
    ``skipped`` so the next tick can resume cleanly even if this
    process crashes mid-phase.

    Returns:

    -   ``TickResult(ok=True, outcome="ok")`` once every phase is
        ``ok`` / ``skipped`` and the entry is in ``## Done``.
    -   ``TickResult(ok=False, outcome=…)`` as soon as any phase
        fails — the failure is sticky in ``phases.json`` until the
        next retry overwrites it.

    Side-effects on ``daemon-state.json``:

    -   Calls :func:`daemon_state.update_tick` at every phase
        boundary so the web UI's `Current tick` panel is always
        meaningful.

    Does NOT call :func:`daemon_state.begin_tick` /
    :func:`end_tick` — that bracketing is the caller's job (so a
    crash inside this function still leaves a clean history row).
    """
    logger.info("starting roadmap entry %s", entry.slug)
    if cfg.dry_run:
        logger.info("[dry-run] would run phased pipeline for %s", entry.slug)
        return TickResult(ok=True, outcome="ok", summary="dry-run")

    state = phase_state.load_or_init(
        entry.slug,
        needs_variant=_entry_needs_variant(entry),
    )
    next_phase = state.first_unfinished()
    if next_phase is None:
        logger.info("entry %s is already fully closed; nothing to do", entry.slug)
        return TickResult(
            ok=True,
            outcome="ok",
            summary=f"all phases already ok for {entry.slug}",
        )

    # If the next phase is already failed, decide whether to attempt
    # an auto-repair retry or surface the failure to the block gate.
    #
    # The repair budget is per-phase (see
    # :data:`phase_state.MAX_REPAIRS_PER_PHASE`). On a repair attempt
    # we DELIBERATELY do not call ``reset_phase`` — the
    # ``failure_count`` and ``prior_failures`` fields must survive so
    # the phase handler can splice them into the spawn's
    # ``--repair-context`` argument. ``mark_running`` (called by the
    # handler) clears only ``status`` / ``error`` / timestamps.
    failed_rec = state.get(next_phase)
    if failed_rec.status == "failed":
        budget = phase_state.MAX_REPAIRS_PER_PHASE
        late_run_summary = next_phase == "run" and _timed_out_run_has_summary(failed_rec)
        if next_phase != "preflight" and failed_rec.failure_count > budget and not late_run_summary:
            logger.warning(
                "%s for %s exhausted repair budget (%d failures, max=%d); "
                "leaving sticky-failed for the failure gate",
                next_phase,
                entry.slug,
                failed_rec.failure_count,
                budget,
            )
            err = failed_rec.error or "(no error message recorded)"
            return TickResult(
                ok=False,
                outcome="error",
                summary=(
                    f"{next_phase} failed after {failed_rec.failure_count} "
                    f"attempt(s); repair budget exhausted: "
                    f"{_summary_truncate(err, n=160)}"
                ),
            )
        if late_run_summary:
            logger.info(
                "retrying run for %s despite exhausted repair budget; "
                "detached run summary now exists",
                entry.slug,
            )
        elif next_phase == "preflight":
            logger.info(
                "retrying preflight for %s despite %d prior failure(s); "
                "preflight is host-state dependent and may recover after cleanup",
                entry.slug,
                failed_rec.failure_count,
            )
        logger.info(
            "retrying %s for %s (repair attempt %d of %d)",
            next_phase,
            entry.slug,
            failed_rec.failure_count + 1,
            budget + 1,
        )

    for phase_name, handler in _PHASE_DISPATCH:
        rec = state.get(phase_name)
        if rec.status in ("ok", "skipped"):
            continue
        logger.info("→ phase %s for %s", phase_name, entry.slug)
        # Reload state on each phase entry — earlier phases mutated it.
        state = phase_state.load_or_init(entry.slug)
        result = handler(entry, state, cfg)
        if result is not None:
            return result
        # Re-read so the next iteration sees the just-written payload.
        state = phase_state.load_or_init(entry.slug)
        pause_result = _pause_after_phase_if_requested(
            entry=entry,
            phase_name=phase_name,
        )
        if pause_result is not None:
            return pause_result

    # All phases ok / skipped.
    summary = state.get("critique").payload.get("instance_id") or entry.slug
    return TickResult(
        ok=True,
        outcome="ok",
        summary=f"runs/experiments/{summary}",
    )


def _select_next_entry(
    ready: list[RoadmapEntry],
    state: ds.DaemonState,
) -> RoadmapEntry | None:
    """Pick the next entry to process given current state.

    - ``paused``      → never picks anything.
    - ``manual``      → picks the highest-priority entry whose slug
                        is in ``approved_slugs``. (Order = roadmap
                        order, not approval order, so the operator
                        can approve out of order without changing
                        the queue.)
    - ``autonomous``  → picks the first non-blocked ready entry.
    """
    if state.mode == "paused":
        return None
    blocked = _blocked_failure_slugs(state)
    ready = [entry for entry in ready if entry.slug not in blocked]
    if state.mode == "autonomous":
        return ready[0] if ready else None
    # manual mode
    approved = set(state.approved_slugs)
    for e in ready:
        if e.slug in approved:
            return e
    return None


def _blocked_failure_slugs(state: ds.DaemonState) -> set[str]:
    """Return slugs that exhausted the daemon failure gate.

    The roadmap is reconciled through the replan/finalize PR path, not
    by direct daemon edits on ``main``. Blocking is therefore a runtime
    decision: skip the slug until ``lab daemon reset-failures`` clears it.
    """
    return {
        slug
        for slug, rec in state.entry_failures.items()
        if rec.count >= state.max_failures_before_demote
    }


def _record_failure_block(entry: RoadmapEntry, failure_rec: ds.FailureRecord) -> None:
    """Append a synthetic history row when the exit gate blocks a slug."""
    logger.warning(
        "blocked %s after %d consecutive %s failures; roadmap left unchanged",
        entry.slug,
        failure_rec.count,
        failure_rec.last_outcome,
    )
    with ds.mutate(actor="daemon") as st:
        st.history.append(
            ds.TickHistoryEntry(
                slug=entry.slug,
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                outcome="blocked",
                phase_reached="done",
                duration_sec=0.0,
                summary=(
                    f"blocked after {failure_rec.count} "
                    f"{failure_rec.last_outcome} failures; reset failures to retry"
                ),
            )
        )


def _idle_log(reason: str, sleep_sec: int) -> None:
    """Log an idle reason at most once per minute to avoid journal spam."""
    now = time.monotonic()
    last = getattr(_idle_log, "_last_at", 0.0)
    last_reason = getattr(_idle_log, "_last_reason", None)
    if now - last > 60 or reason != last_reason:
        logger.info("%s; sleeping %ds (or until SIGUSR1)", reason, sleep_sec)
        _idle_log._last_at = now  # type: ignore[attr-defined]
        _idle_log._last_reason = reason  # type: ignore[attr-defined]


def _idle_wait(seconds: float) -> bool:
    """Sleep up to ``seconds`` seconds; return early on SIGUSR1.

    Returns ``True`` if interrupted by ``SIGUSR1`` (i.e. a CLI/UI
    mutation called :func:`daemon_state.notify_daemon`), ``False`` if
    the full ``seconds`` elapsed.

    Uses ``threading.Event.wait`` rather than ``time.sleep`` because
    ``Event.wait`` is interruptible by signals that set the event
    from a handler — see :func:`_install_signal_cleanup`. ``time.sleep``
    in CPython 3 does NOT return early on signal delivery (PEP 475
    auto-retries the syscall), so we'd otherwise lose the snappy
    wake-up.
    """
    woken = _WAKE_EVENT.wait(timeout=seconds)
    if woken:
        _WAKE_EVENT.clear()
        logger.debug("idle wait interrupted by SIGUSR1; re-checking state")
    return woken


def loop(cfg: OrchestratorConfig | None = None) -> None:
    """Main daemon loop. Consults :mod:`daemon_state` every tick.

    The loop never directly mutates the markdown roadmap (that is the
    replan/finalize PR path's job). When an entry has failed too many
    times in a row, the loop records it as blocked in daemon state and
    skips it until an operator resets the failure counter.
    """
    cfg = cfg or OrchestratorConfig()
    while True:
        state = ds.load()
        entries = parse_up_next()
        blocked = _blocked_failure_slugs(state)
        blocked_ready = [e for e in entries if e.slug in blocked and is_dependency_satisfied(e)]
        ready = [e for e in entries if e.slug not in blocked and is_dependency_satisfied(e)]
        entry = _select_next_entry(ready, state)
        if entry is None:
            if cfg.once:
                logger.info("nothing ready, exiting (--once)")
                return
            if state.mode == "paused":
                _idle_log("daemon mode=paused", cfg.idle_sleep_sec)
            elif state.mode == "manual" and ready and not state.approved_slugs:
                _idle_log(
                    f"daemon mode=manual; {len(ready)} ready entries but no approvals",
                    cfg.idle_sleep_sec,
                )
            elif state.mode == "manual":
                _idle_log(
                    "daemon mode=manual; no approved+ready entry to run",
                    cfg.idle_sleep_sec,
                )
            elif blocked_ready:
                _idle_log(
                    f"{len(blocked_ready)} ready roadmap entr"
                    f"{'y is' if len(blocked_ready) == 1 else 'ies are'} "
                    "blocked by failure counters",
                    cfg.idle_sleep_sec,
                )
            else:
                _idle_log("no ready roadmap entries", cfg.idle_sleep_sec)
            _idle_wait(cfg.idle_sleep_sec)
            continue

        # Manual mode: consume the approval up front. If anything
        # explodes inside _process_entry, we'd rather force the
        # operator to re-approve than have the daemon retry on its
        # own — that's the whole point of "consumed" approvals.
        if state.mode == "manual":
            ds.consume_approval(entry.slug, actor="daemon")

        ds.begin_tick(
            ds.ActiveTick(
                slug=entry.slug,
                phase="spawning",
                started_at=datetime.now(timezone.utc),
            ),
            actor="daemon",
        )

        result: TickResult
        try:
            result = _process_entry(entry, cfg)
        except codex_adapter.CodexAdapterError as exc:
            logger.error("codex adapter error for %s: %s", entry.slug, exc)
            result = TickResult(ok=False, outcome="error", summary=f"codex: {exc}")
        except Exception as exc:
            logger.exception("unhandled error processing %s", entry.slug)
            result = TickResult(ok=False, outcome="error", summary=f"unhandled: {exc}")

        # End-of-tick bookkeeping (history + failure counter).
        _, failure_rec = ds.end_tick(
            outcome=result.outcome,
            summary=result.summary,
            actor="daemon",
        )

        # Exit gate: applies in BOTH modes. It no longer mutates
        # roadmap markdown directly on main; blocked entries are
        # skipped by the selection step until the operator resets the
        # failure counter.
        if failure_rec is not None and failure_rec.count >= state.max_failures_before_demote:
            _record_failure_block(entry, failure_rec)

        if cfg.once:
            return


# ----- foreground / daemon entrypoints (called by `uv run lab daemon …`) ---


def _foreground_log_init(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _install_signal_cleanup() -> None:
    """Install SIGTERM / SIGINT / SIGUSR1 handlers.

    - **SIGTERM / SIGINT**: convert into ``KeyboardInterrupt`` so the
      lock context manager's ``finally`` block runs and unlinks the
      lock. Without this, the default Python SIGTERM handler exits
      the interpreter without running ``__exit__``, leaving a stale
      ``runs/lab/orchestrator.lock`` behind and breaking
      ``systemctl --user restart openharness-daemon`` with
      "Orchestrator lock already held". SIGTERM is what systemd
      (and ``daemon stop``) sends; SIGINT is Ctrl-C in the
      foreground case.

    - **SIGUSR1**: set the module-level ``_WAKE_EVENT`` so the
      currently-blocked ``_idle_wait`` call returns immediately. Sent
      by :func:`daemon_state.notify_daemon` from the CLI / web UI
      whenever the operator changes mode or approves a slug — this
      is what makes UI clicks feel snappy instead of "wait up to 5
      minutes for the next tick". Safe inside a signal handler:
      ``Event.set`` is async-signal-safe and acquires no Python
      locks the handler can deadlock on.
    """

    def _raise(_signum: int, _frame: object) -> None:
        # Raising KeyboardInterrupt here propagates out of ``loop``,
        # unwinds through ``orchestrator_lock``'s finally, then
        # bubbles up so the typer CLI exits with the conventional
        # 130 (SIGINT) status. systemd sees that as a clean stop
        # because we used ``Restart=on-failure`` (not always).
        raise KeyboardInterrupt

    def _wake(_signum: int, _frame: object) -> None:
        _WAKE_EVENT.set()

    signal.signal(signal.SIGTERM, _raise)
    signal.signal(signal.SIGINT, _raise)
    signal.signal(signal.SIGUSR1, _wake)


def start(*, foreground: bool = True, once: bool = False, dry_run: bool = False) -> None:
    """Start the orchestrator in the current process.

    `once=True` runs at most one roadmap entry and exits — useful for
    smoke tests. `foreground=True` keeps the loop attached to the
    terminal; the typer CLI shells out via tmux for true daemon mode.
    """
    ensure_lab_runs_dir()
    _foreground_log_init()
    _install_signal_cleanup()
    cfg = OrchestratorConfig(once=once, dry_run=dry_run)
    try:
        with codex_adapter.orchestrator_lock(owner="lab-runner"):
            # Boot-time hygiene on daemon-state.json:
            # - Clear any leftover active_tick from a previous crash;
            #   the tick that was in flight is gone, no point pretending.
            # - The mode survives restarts (it's the operator's
            #   declared intent, not a runtime fact).
            ds.clear_active_tick(actor="daemon-boot")
            state = ds.load()
            logger.info(
                "orchestrator started (pid=%d, once=%s, dry_run=%s, mode=%s, "
                "approvals=%d, max_failures_before_demote=%d)",
                os.getpid(),
                once,
                dry_run,
                state.mode,
                len(state.approved_slugs),
                state.max_failures_before_demote,
            )
            loop(cfg)
    except KeyboardInterrupt:
        # Caught here (rather than letting it propagate to the typer
        # entry point) so we get one tidy log line + exit 0. The lock
        # has already been released by orchestrator_lock's finally.
        # Also drop any in-flight active_tick — the codex spawn under
        # it (if any) was killed by systemd's SIGTERM cascade.
        try:
            ds.clear_active_tick(actor="daemon-shutdown")
        except Exception:
            logger.exception("failed to clear active_tick during shutdown")
        logger.info("orchestrator received signal, exiting cleanly")


def stop() -> None:
    """SIGTERM the recorded orchestrator pid."""
    if not ORCHESTRATOR_LOCK_PATH.is_file():
        logger.info("no orchestrator lock at %s; nothing to stop", ORCHESTRATOR_LOCK_PATH)
        return
    payload = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    pid = int(payload.get("pid") or 0)
    if not pid:
        logger.warning("malformed lock; removing")
        ORCHESTRATOR_LOCK_PATH.unlink()
        return
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("sent SIGTERM to orchestrator pid=%d", pid)
    except ProcessLookupError:
        logger.warning("pid %d gone; removing stale lock", pid)
        ORCHESTRATOR_LOCK_PATH.unlink()


def status() -> dict:
    if not ORCHESTRATOR_LOCK_PATH.is_file():
        return {"running": False}
    try:
        payload = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"running": False, "lock_corrupted": True}
    pid = int(payload.get("pid") or 0)
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            alive = False
    return {"running": alive, "pid": pid, "lock": payload}
