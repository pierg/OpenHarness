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
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([
            str(parent / "src"),
            "/opt/other",
            str(worktree / "src"),
        ]),
    )
    (worktree / "src").mkdir()

    env = phase_run._exec_env(worktree)

    path_entries = env["PATH"].split(os.pathsep)
    pythonpath_entries = env["PYTHONPATH"].split(os.pathsep)
    assert "VIRTUAL_ENV" not in env
    assert "VIRTUAL_ENV_PROMPT" not in env
    assert path_entries[0] == str(worktree_venv_bin.resolve())
    assert str(parent_venv_bin) not in path_entries
    assert path_entries.count(str(worktree_venv_bin)) == 1
    assert pythonpath_entries[0] == str((worktree / "src").resolve())
    assert str((parent / "src").resolve()) not in pythonpath_entries
    assert pythonpath_entries.count(str((worktree / "src").resolve())) == 1


def test_exec_env_prefers_repo_gemini_run_key(monkeypatch, tmp_path: Path) -> None:
    from openharness.lab import phase_run

    parent = tmp_path / "parent"
    worktree = tmp_path / "worktree"
    parent.mkdir()
    worktree.mkdir()
    (parent / ".env").write_text(
        "\n".join(
            [
                "GOOGLE_API_KEY=from-dotenv-google",
                "GEMINI_API_KEY=from-dotenv-gemini",
                "GEMINI_API_KEY_RUN=from-dotenv-run",
                "OPENHARNESS_EXAMPLE=from-dotenv",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(phase_run, "REPO_ROOT", parent)
    monkeypatch.setenv("GOOGLE_API_KEY", "from-shell-google")
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell-gemini")
    monkeypatch.setenv("OPENHARNESS_EXAMPLE", "from-shell")

    env = phase_run._exec_env(worktree)

    assert env["GOOGLE_API_KEY"] == "from-dotenv-run"
    assert env["GEMINI_API_KEY"] == "from-dotenv-run"
    assert env["GEMINI_API_KEY_RUN"] == "from-dotenv-run"
    assert env["OPENHARNESS_EXAMPLE"] == "from-shell"


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


def test_run_experiment_records_launch_before_polling(monkeypatch, tmp_path: Path) -> None:
    from openharness.lab import phase_run

    worktree = tmp_path / "worktree"
    (worktree / "experiments").mkdir(parents=True)
    (worktree / "experiments" / "alpha.yaml").write_text("legs: []\n")
    runs_root = tmp_path / "runs" / "experiments"
    logs_root = tmp_path / "runs" / "lab" / "logs"
    monkeypatch.setattr(phase_run, "EXPERIMENTS_RUNS_ROOT", runs_root)
    monkeypatch.setattr(phase_run, "LAB_LOGS_DIR", logs_root)
    monkeypatch.setattr(
        phase_run,
        "_instance_id_for",
        lambda _spec, profile=None: "alpha-20260424-000000",
    )

    launched = []

    class DummyProc:
        pid = 123

    def fake_spawn(**kwargs):
        run_dir = kwargs["run_dir"]
        run_dir.mkdir(parents=True)
        (run_dir / "results").mkdir()
        (run_dir / "results" / "summary.md").write_text("ok\n")
        (run_dir / "experiment.json").write_text(json.dumps({
            "legs": [{
                "leg_id": "basic",
                "status": "succeeded",
                "trials": [{"trial_id": "t1"}],
                "aggregate": {"n_trials": 1},
            }],
        }))
        return DummyProc()

    monkeypatch.setattr(phase_run, "_spawn_exec", fake_spawn)

    outcome = phase_run.run_experiment(
        slug="alpha",
        worktree=worktree,
        poll_interval_sec=0,
        on_launch=launched.append,
    )

    assert outcome.instance_id == "alpha-20260424-000000"
    assert len(launched) == 1
    assert launched[0].instance_id == outcome.instance_id
    assert launched[0].run_dir == outcome.run_dir
