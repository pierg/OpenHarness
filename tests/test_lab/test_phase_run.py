from __future__ import annotations

import os
from pathlib import Path


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
