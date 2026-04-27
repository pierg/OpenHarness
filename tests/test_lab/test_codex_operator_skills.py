from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def isolated_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ModuleType]:
    repo = tmp_path / "repo"
    (repo / "lab").mkdir(parents=True)
    (repo / "runs" / "lab").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("# placeholder")

    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))

    import openharness.lab.paths as paths

    importlib.reload(paths)
    import openharness.lab.codex as codex

    importlib.reload(codex)

    return repo, codex


def test_ensure_skill_path_deploys_operator_skills(
    isolated_codex: tuple[Path, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, codex = isolated_codex
    binary = tmp_path / "skills"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return str(binary) if name == "skills" else None

    def fake_run(
        cmd: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[1] == "resolve":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "repo": "pierg/OpenHarness",
                        "registered": True,
                    }
                ),
                stderr="",
            )
        if cmd[1] == "deploy":
            skill_dir = repo / ".agents" / "skills" / "lab"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: lab\n---\n")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(codex.shutil, "which", fake_which)
    monkeypatch.setattr(codex.subprocess, "run", fake_run)

    codex._ensure_skill_path("lab")

    assert calls == [
        [str(binary), "resolve", "--json", str(repo)],
        [str(binary), "deploy", "--json", "pierg/OpenHarness"],
    ]
    assert (repo / ".agents" / "skills" / "lab" / "SKILL.md").is_file()


def test_ensure_skill_path_deploys_into_worktree_with_parent_repo_key(
    isolated_codex: tuple[Path, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, codex = isolated_codex
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    binary = tmp_path / "skills"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    calls: list[tuple[list[str], str | None]] = []

    def fake_which(name: str) -> str | None:
        return str(binary) if name == "skills" else None

    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, str(kwargs.get("cwd")) if kwargs.get("cwd") else None))
        if cmd[1] == "resolve" and cmd[-1] == str(worktree):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "registered": False}),
                stderr="",
            )
        if cmd[1] == "resolve" and cmd[-1] == str(repo):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "repo": "pierg/OpenHarness",
                        "registered": True,
                    }
                ),
                stderr="",
            )
        if cmd[1] == "deploy":
            skill_dir = worktree / ".agents" / "skills" / "lab"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: lab\n---\n")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(codex.shutil, "which", fake_which)
    monkeypatch.setattr(codex.subprocess, "run", fake_run)

    codex._ensure_skill_path("lab", checkout_root=worktree)

    assert [c[0] for c in calls] == [
        [str(binary), "resolve", "--json", str(worktree)],
        [str(binary), "resolve", "--json", str(repo)],
        [str(binary), "deploy", "--json", "pierg/OpenHarness"],
    ]
    assert calls[-1][1] == str(worktree)
    assert (worktree / ".agents" / "skills" / "lab" / "SKILL.md").is_file()


def test_ensure_skill_path_raises_when_operator_deploy_fails(
    isolated_codex: tuple[Path, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, codex = isolated_codex
    binary = tmp_path / "skills"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    def fake_which(name: str) -> str | None:
        return str(binary) if name == "skills" else None

    def fake_run(
        cmd: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[1] == "resolve":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "repo": "pierg/OpenHarness",
                        "registered": True,
                    }
                ),
                stderr="",
            )
        if cmd[1] == "deploy":
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="deploy boom")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(codex.shutil, "which", fake_which)
    monkeypatch.setattr(codex.subprocess, "run", fake_run)

    with pytest.raises(codex.CodexAdapterError, match="skills deploy pierg/OpenHarness"):
        codex._ensure_skill_path("lab")
