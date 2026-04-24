from __future__ import annotations

import os
import json
from pathlib import Path

import pytest


def test_exec_env_drops_parent_virtualenv(monkeypatch, tmp_path: Path) -> None:
    from openharness.lab import phase_run

    parent = tmp_path / "parent"
    worktree = tmp_path / "worktree"
    parent_venv_bin = parent / ".venv" / "bin"
    worktree_venv_bin = worktree / ".venv" / "bin"
    parent_venv_bin.mkdir(parents=True)
    worktree_venv_bin.mkdir(parents=True)
    monkeypatch.setattr(phase_run, "REPO_ROOT", parent)
    monkeypatch.setenv("VIRTUAL_ENV", str(parent / ".venv"))
    monkeypatch.setenv("VIRTUAL_ENV_PROMPT", "OpenHarness")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([
            str(parent_venv_bin),
            "/usr/local/bin",
            str(worktree_venv_bin),
            "/usr/bin",
        ]),
    )

    env = phase_run._exec_env(worktree)

    path_entries = env["PATH"].split(os.pathsep)
    assert "VIRTUAL_ENV" not in env
    assert "VIRTUAL_ENV_PROMPT" not in env
    assert path_entries[0] == str(worktree_venv_bin.resolve())
    assert str(parent_venv_bin) not in path_entries
    assert path_entries.count(str(worktree_venv_bin)) == 1


def test_validate_complete_run_rejects_no_trial_leg(tmp_path: Path) -> None:
    from openharness.lab import phase_run

    run_dir = tmp_path / "runs" / "experiments" / "bad-run"
    run_dir.mkdir(parents=True)
    (run_dir / "experiment.json").write_text(json.dumps({
        "legs": [
            {
                "leg_id": "control",
                "status": "succeeded",
                "result_status": "partial",
                "trials": [{"trial_id": "t1"}],
                "aggregate": {"n_trials": 1},
            },
            {
                "leg_id": "treatment",
                "status": "failed",
                "result_status": "no_trials",
                "trials": [],
                "aggregate": None,
            },
        ],
    }))

    with pytest.raises(phase_run.PhaseRunError, match="treatment"):
        phase_run._validate_complete_run(run_dir)


def test_validate_complete_run_accepts_partial_leg_with_trials(tmp_path: Path) -> None:
    from openharness.lab import phase_run

    run_dir = tmp_path / "runs" / "experiments" / "ok-run"
    run_dir.mkdir(parents=True)
    (run_dir / "experiment.json").write_text(json.dumps({
        "legs": [
            {
                "leg_id": "control",
                "status": "succeeded",
                "result_status": "partial",
                "trials": [{"trial_id": "t1", "error": {"phase": "agent"}}],
                "aggregate": {"n_trials": 1, "n_errored": 1},
            }
        ],
    }))

    phase_run._validate_complete_run(run_dir)
