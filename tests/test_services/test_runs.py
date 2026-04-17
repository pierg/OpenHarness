"""Tests for openharness.services.runs."""

from __future__ import annotations

import json
import re
from pathlib import Path

from openharness.services.runs import create_run_artifacts, generate_run_id, save_run_manifest


def test_generate_run_id_format():
    run_id = generate_run_id()
    assert re.fullmatch(r"run-oh-\d{4}-\d{6}-[0-9a-f]{4}", run_id)


def test_generate_run_id_unique():
    assert generate_run_id() != generate_run_id()


def test_create_run_artifacts_minimal(tmp_path: Path):
    run = create_run_artifacts(tmp_path, run_id="run-abc123def456")
    assert run.run_id == "run-abc123def456"
    assert run.run_dir == tmp_path.resolve() / "runs" / "run-abc123def456"
    assert run.run_dir.is_dir()
    assert run.messages_path == run.run_dir / "messages.jsonl"
    assert run.events_path == run.run_dir / "events.jsonl"
    assert run.results_path == run.run_dir / "results.json"
    assert run.metrics_path == run.run_dir / "metrics.json"
    assert run.logs_dir is None
    assert run.workspace_dir is None


def test_create_run_artifacts_with_logs_and_workspace(tmp_path: Path):
    run = create_run_artifacts(
        tmp_path, run_id="run-abc123def456", with_logs=True, with_workspace=True
    )
    assert run.logs_dir == run.run_dir / "logs"
    assert run.workspace_dir == run.run_dir / "workspace"
    assert run.logs_dir is not None and run.logs_dir.is_dir()
    assert run.workspace_dir is not None and run.workspace_dir.is_dir()


def test_create_run_artifacts_records_existing_workspace(tmp_path: Path):
    workspace = tmp_path / "runs" / "run-abc123def456" / "workspace"
    run = create_run_artifacts(
        tmp_path,
        run_id="run-abc123def456",
        workspace_dir=workspace,
    )

    assert run.workspace_dir == workspace.resolve()
    assert run.workspace_dir.is_dir()


def test_create_run_artifacts_generates_run_id_when_omitted(tmp_path: Path):
    run = create_run_artifacts(tmp_path)
    assert run.run_id.startswith("run-")
    assert run.run_dir.is_dir()


def test_metadata_path(tmp_path: Path):
    run = create_run_artifacts(tmp_path, run_id="run-abc123def456")
    assert run.metadata_path == run.run_dir / "run.json"


def test_save_run_manifest_from_artifacts(tmp_path: Path):
    run = create_run_artifacts(tmp_path, run_id="run-abc123def456")
    path = save_run_manifest(run, {"status": "completed", "tokens": 42})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"status": "completed", "tokens": 42}
    assert path == run.metadata_path


def test_save_run_manifest_from_path(tmp_path: Path):
    run_dir = tmp_path / "runs" / "run-xyz"
    path = save_run_manifest(run_dir, {"status": "ok"})
    assert path == run_dir / "run.json"
    assert json.loads(path.read_text())["status"] == "ok"
