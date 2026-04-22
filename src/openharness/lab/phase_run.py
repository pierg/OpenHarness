"""Deterministic Phase 3 — launch the experiment and wait for results.

Replaces the launch-and-poll responsibility that lived inside the old
``lab-run-experiment`` codex skill. There is no LLM judgment in this
phase: by the time the orchestrator gets here, every input has been
decided by earlier phases (preflight chose the worktree; implement
wrote the experiment YAML and validated it).

Where things land
-----------------

The experiment YAML and any variant code live in the **worktree**
(``../OpenHarness.worktrees/lab-<slug>/``) — they're the artifacts the
PR will eventually contain.

The run output (``runs/experiments/<instance-id>/`` plus log files)
lands in the **main repo's** ``runs/`` directory regardless of where
``uv run exec`` is launched from. We force this with ``--root`` so:

-   The existing lab infra (``ingest``, critic spawns, dashboards)
    keeps working without per-worktree path patches.
-   ``runs/`` is gitignored in main, so the output never accidentally
    pollutes the worktree's branch.
-   Multiple in-flight worktrees can't collide on instance ids — the
    run dir name is timestamp-suffixed and lives in one shared place.

Resume semantics
----------------

If ``phases.json`` already records this phase as ``running`` with a
known ``instance_id``, we skip the launch and re-enter the polling
loop directly. The original subprocess (if any) keeps running
independently of the daemon thanks to ``start_new_session=True``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

from openharness.lab import lab_docs
from openharness.lab.paths import EXPERIMENTS_RUNS_ROOT, LAB_LOGS_DIR, REPO_ROOT

logger = logging.getLogger(__name__)


DEFAULT_RUN_TIMEOUT_SEC: int = 4 * 60 * 60   # 4 h cap on a single run.
DEFAULT_POLL_INTERVAL_SEC: int = 60


class PhaseRunError(RuntimeError):
    """Raised when the run phase cannot launch or never produces results."""


@dataclass(slots=True, frozen=True)
class RunOutcome:
    """What the runner records into the run-phase payload."""

    instance_id: str
    run_dir: Path
    spec_name: str
    log_path: Path


# ---------------------------------------------------------------------------
# Spec resolution
# ---------------------------------------------------------------------------


def _resolve_spec(spec_name: str, *, worktree: Path) -> Path:
    """Return the absolute path to ``experiments/<spec_name>.yaml`` (or .yml).

    The implement phase is responsible for creating the spec file (and
    any agent YAMLs it references) inside the worktree. We resolve it
    here so the launcher can fail fast with a clear error if the
    implement phase forgot.
    """
    exp_dir = worktree / "experiments"
    if not exp_dir.is_dir():
        raise PhaseRunError(
            f"Worktree {worktree} has no experiments/ directory; "
            "the implement phase did not produce a spec."
        )
    for ext in (".yaml", ".yml"):
        candidate = exp_dir / f"{spec_name}{ext}"
        if candidate.is_file():
            return candidate
    raise PhaseRunError(
        f"Experiment spec not found: {exp_dir}/{spec_name}.yaml. "
        f"Available: {sorted(p.name for p in exp_dir.glob('*.y*ml'))}"
    )


# ---------------------------------------------------------------------------
# Journal entry helper
# ---------------------------------------------------------------------------


def append_journal_stub(
    *,
    slug: str,
    type_: str,
    trunk_id: str,
    mutation: str | None,
    hypothesis: str,
    branch: str,
    repo_root: Path = REPO_ROOT,
) -> None:
    """Append the empty-shell journal entry for ``slug`` to ``lab/experiments.md``.

    The ``Run:`` bullet is left as a placeholder until ``set_journal_run_path``
    is called once the harbor process has settled on an instance id.

    The ``Branch:`` bullet is recorded immediately — by phase 3 the
    branch already exists in the worktree, even if no PR has been
    opened yet. Finalize will rewrite it later with the PR URL or a
    "not opened" reason.

    Idempotent: a previous tick may have stubbed this entry already,
    in which case we no-op.
    """
    lab_root = repo_root / "lab"
    if lab_docs.journal_entry_exists(slug, lab_root=lab_root):
        logger.info("journal entry for %s already exists; skipping append", slug)
        return
    trunk_md = (
        f"[`{trunk_id}`](../src/openharness/agents/configs/{trunk_id}.yaml)"
        if not trunk_id.startswith("[") else trunk_id
    )
    lab_docs.append_journal_entry(
        slug=slug,
        type_=type_,
        trunk_at_runtime=trunk_md,
        mutation=mutation,
        hypothesis=hypothesis,
        run_path=None,
        branch=branch,
        on_date=date_cls.today(),
        lab_root=lab_root,
    )


# ---------------------------------------------------------------------------
# Subprocess launch
# ---------------------------------------------------------------------------


def _instance_id_for(spec_name: str, *, profile: str | None) -> str:
    """Reproduce ``exec``'s default instance_id format so we can predict it.

    ``uv run exec`` derives the instance id from
    ``<spec_name><-profile><-timestamp>``. We mirror that calculation
    here (with second-precision UTC) before launching, so the runner
    can locate the run directory immediately rather than scanning
    after the fact.
    """
    suffix = f"-{profile}" if profile else ""
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{spec_name}{suffix}-{ts}"


def _spawn_exec(
    *,
    spec_name: str,
    profile: str | None,
    instance_id: str,
    worktree: Path,
    run_dir: Path,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    """Launch ``uv run exec`` inside ``worktree`` as a detached process.

    Detaches via ``start_new_session=True`` so a SIGTERM to the
    daemon (e.g. ``systemctl restart``) does NOT cascade to harbor.
    The child gets its own process group; its stdout/stderr are
    redirected to ``log_path`` so the operator can tail it
    independently of the daemon's logs.

    We pass ``--root <run_dir>`` (an absolute path under the **main**
    repo's ``runs/experiments/``) so the run output never lands inside
    the worktree. See module docstring for rationale.
    """
    args: list[str] = [
        "uv", "run", "exec", spec_name,
        "--instance-id", instance_id,
        "--root", str(run_dir),
    ]
    if profile:
        args += ["--profile", profile]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    log_fp = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(worktree),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    finally:
        # The child inherits the fd; closing in the parent is safe and
        # avoids leaking an open fd in the orchestrator process.
        log_fp.close()
    logger.info(
        "launched exec pid=%d in %s; logging to %s; run_dir=%s",
        proc.pid, worktree, log_path, run_dir,
    )
    return proc


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------


def _summary_path(run_dir: Path) -> Path:
    return run_dir / "results" / "summary.md"


def wait_for_summary(
    run_dir: Path,
    *,
    timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC,
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
) -> bool:
    """Block until ``results/summary.md`` exists or ``timeout_sec`` elapses."""
    summary = _summary_path(run_dir)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if summary.is_file():
            return True
        time.sleep(poll_interval_sec)
    return summary.is_file()


# ---------------------------------------------------------------------------
# Public entry point — runner.py calls this once per tick that needs phase 3
# ---------------------------------------------------------------------------


def run_experiment(
    *,
    slug: str,
    worktree: Path,
    spec_name: str | None = None,
    profile: str | None = None,
    timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC,
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
    resume_instance_id: str | None = None,
) -> RunOutcome:
    """Launch the experiment in ``worktree`` and wait for ``summary.md``.

    ``spec_name`` defaults to ``slug``: the convention is that the
    implement phase writes the experiment spec to
    ``experiments/<slug>.yaml`` inside the worktree. Pass an explicit
    ``spec_name`` only when the slug differs from the spec filename
    (e.g. baseline runs reusing ``tb2-baseline.yaml``).

    ``resume_instance_id`` lets the runner re-enter the polling loop
    after a daemon restart without re-launching the harbor process.
    """
    spec = spec_name or slug
    _resolve_spec(spec, worktree=worktree)  # fail fast if missing

    if resume_instance_id:
        instance_id = resume_instance_id
        logger.info("resuming poll on existing instance_id=%s", instance_id)
    else:
        instance_id = _instance_id_for(spec, profile=profile)

    run_dir = EXPERIMENTS_RUNS_ROOT / instance_id
    log_path = LAB_LOGS_DIR / "exec" / f"{instance_id}.log"

    if not resume_instance_id:
        _spawn_exec(
            spec_name=spec, profile=profile, instance_id=instance_id,
            worktree=worktree, run_dir=run_dir, log_path=log_path,
        )

    if not wait_for_summary(
        run_dir,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
    ):
        raise PhaseRunError(
            f"results/summary.md never landed in {run_dir} within "
            f"{timeout_sec}s. Check {log_path} and the run's "
            "events.jsonl for what happened."
        )

    logger.info("run %s complete; summary at %s", instance_id, _summary_path(run_dir))
    return RunOutcome(
        instance_id=instance_id,
        run_dir=run_dir,
        spec_name=spec,
        log_path=log_path,
    )
