"""Canonical filesystem locations for the lab pipeline.

All machine-local lab state (DuckDB file, codex spawn logs, the
orchestrator's file lock) lives under `runs/lab/` so it inherits the
existing `runs/` gitignore entry. The human-facing audit surface
(`lab/*.md`) lives under `lab/` at the repo root.

The repo root is detected by walking up from this file. Override it by
setting `OPENHARNESS_REPO_ROOT` if you embed the package somewhere
unusual.
"""

from __future__ import annotations

import os
from pathlib import Path


def _detect_repo_root() -> Path:
    override = os.environ.get("OPENHARNESS_REPO_ROOT")
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "lab").is_dir():
            return parent
    return Path.cwd().resolve()


REPO_ROOT: Path = _detect_repo_root()

LAB_ROOT: Path = REPO_ROOT / "lab"
"""Human-facing lab markdown surface (ideas/roadmap/experiments/components)."""

RUNS_ROOT: Path = REPO_ROOT / "runs"

LAB_RUNS_ROOT: Path = RUNS_ROOT / "lab"
"""Machine-local lab state. Already gitignored via `runs/`."""

LAB_DB_PATH: Path = LAB_RUNS_ROOT / "trials.duckdb"
LAB_LOGS_DIR: Path = LAB_RUNS_ROOT / "logs"
ORCHESTRATOR_LOCK_PATH: Path = LAB_RUNS_ROOT / "orchestrator.lock"

EXPERIMENTS_RUNS_ROOT: Path = RUNS_ROOT / "experiments"


def ensure_lab_runs_dir() -> Path:
    LAB_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    LAB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LAB_RUNS_ROOT
