"""Helpers for storing per-run artifacts under a unified runs directory.

Each automated agent run gets its own subdirectory under the project's ``runs/``
folder (resolved by ``get_project_runs_dir``).  The layout is:

.. code-block:: text

    <cwd>/runs/<run-id>/
        run.json        ← metadata manifest written by save_run_manifest
        messages.jsonl  ← conversation transcript records
        events.jsonl    ← stream/tool execution records
        results.json    ← final result payload
        metrics.json    ← usage + execution summary payload
        logs/           ← optional auxiliary logs, created when with_logs=True
        workspace/      ← optional, created when with_workspace=True
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.config.paths import get_project_runs_dir


def generate_run_id() -> str:
    """Return a time-sortable run identifier suitable for folder names and trace IDs."""
    timestamp = datetime.now().strftime("%m%d-%H%M%S")
    return f"run-oh-{timestamp}-{uuid4().hex[:4]}"


@dataclass(frozen=True)
class RunArtifacts:
    """Filesystem layout for a single OpenHarness run."""

    run_id: str
    run_dir: Path
    messages_path: Path
    events_path: Path
    results_path: Path
    metrics_path: Path
    logs_dir: Path | None = None
    workspace_dir: Path | None = None

    @property
    def metadata_path(self) -> Path:
        """Canonical path for the run manifest JSON file."""
        return self.run_dir / "run.json"


def create_run_artifacts(
    cwd: str | Path,
    *,
    run_id: str | None = None,
    with_logs: bool = False,
    with_workspace: bool = False,
    workspace_dir: str | Path | None = None,
) -> RunArtifacts:
    """Create the directory layout for one run and return a ``RunArtifacts`` handle.

    Args:
        cwd: Project root used to locate the ``runs/`` directory.
        run_id: Explicit run identifier; a new one is generated when omitted.
        with_logs: Create a ``logs/`` subdirectory inside the run directory.
        with_workspace: Create a ``workspace/`` subdirectory inside the run directory.
        workspace_dir: Existing workspace directory to record for this run.
    """
    resolved_run_id = run_id or generate_run_id()
    run_dir = get_project_runs_dir(cwd) / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logs_dir: Path | None = None
    if with_logs:
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

    resolved_workspace_dir: Path | None = None
    if workspace_dir is not None:
        resolved_workspace_dir = Path(workspace_dir).expanduser().resolve()
        resolved_workspace_dir.mkdir(parents=True, exist_ok=True)
    elif with_workspace:
        resolved_workspace_dir = run_dir / "workspace"
        resolved_workspace_dir.mkdir(parents=True, exist_ok=True)

    return RunArtifacts(
        run_id=resolved_run_id,
        run_dir=run_dir,
        messages_path=run_dir / "messages.jsonl",
        events_path=run_dir / "events.jsonl",
        results_path=run_dir / "results.json",
        metrics_path=run_dir / "metrics.json",
        logs_dir=logs_dir,
        workspace_dir=resolved_workspace_dir,
    )


def save_run_manifest(run: RunArtifacts | Path, payload: dict[str, Any]) -> Path:
    """Write *payload* as a JSON manifest for *run*.

    Args:
        run: A ``RunArtifacts`` instance or a raw directory ``Path``.
        payload: Arbitrary JSON-serialisable data to record.

    Returns:
        The path of the written manifest file.
    """
    if isinstance(run, RunArtifacts):
        path = run.metadata_path
    else:
        run_dir = Path(run)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "run.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path
