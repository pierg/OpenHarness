from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest


def _trial_dir(tmp_path: Path) -> Path:
    trial = tmp_path / "runs" / "experiments" / "inst" / "legs" / "basic" / "harbor" / "h" / "task__1"
    trial.mkdir(parents=True)
    (trial.parents[2] / "agent.resolved.yaml").write_text(
        "name: basic\narchitecture: simple\nmodel: gemini-x\ntools: [bash]\ncomponents: [loop-guard]\n"
    )
    (trial / "agent").mkdir()
    (trial / "agent" / "trajectory.json").write_text(json.dumps({
        "steps": [
            {"step_id": 1, "source": "user", "message": "Fix the bug."},
            {
                "step_id": 2,
                "source": "agent",
                "message": "(tool call)",
                "tool_calls": [{
                    "function_name": "bash",
                    "arguments": {"command": "pytest -q"},
                }],
                "observation": {"results": [{"content": "failed"}]},
            },
            {
                "step_id": 3,
                "source": "agent",
                "message": "(tool call)",
                "tool_calls": [{
                    "function_name": "bash",
                    "arguments": {"command": "pytest -q"},
                }],
                "observation": {"results": [{"content": "failed again"}]},
            },
        ],
    }))
    (trial / "events.jsonl").write_text(
        json.dumps({
            "type": "model_request",
            "model": "gemini-x",
            "usage": {"input_tokens": 10, "output_tokens": 3},
        })
        + "\n"
    )
    (trial / "result.json").write_text(json.dumps({
        "id": "trial-1",
        "task_name": "task",
        "task_checksum": "abc",
        "verifier_result": "failed",
        "agent_result": {
            "metadata": {
                "summary": {
                    "final_text": "I tried",
                    "input_tokens": 10,
                    "output_tokens": 3,
                }
            }
        },
        "verifier": {
            "reward": 0.0,
            "metadata": {
                "parser_results": {
                    "tests": [{
                        "name": "test_bug",
                        "status": "failed",
                        "message": "still broken",
                    }]
                }
            },
        },
    }))
    return trial


def test_trial_evidence_digest_summarizes_core_artifacts(tmp_path: Path) -> None:
    from openharness.lab import trial_evidence

    trial = _trial_dir(tmp_path)
    digest = trial_evidence.build_trial_evidence(trial)

    assert digest["outcome"] == "failed"
    assert digest["agent_config"]["components"] == ["loop-guard"]
    assert digest["trajectory"]["task_instruction"] == "Fix the bug."
    assert digest["trajectory"]["repeated_tool_calls"][0]["count"] == 2
    assert digest["events"]["input_tokens"] == 10
    assert digest["verifier"]["failed_tests"][0]["name"] == "test_bug"


def test_gemini_trial_critic_parses_and_persists_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".agents" / "skills" / "trial-critic").mkdir(parents=True)
    (repo / ".agents" / "skills" / "trial-critic" / "SKILL.md").write_text(
        "---\nname: trial-critic\n---\n"
    )
    (repo / "runs" / "lab").mkdir(parents=True)
    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))

    import openharness.lab.paths as paths
    importlib.reload(paths)
    import openharness.lab.codex as codex
    importlib.reload(codex)
    import openharness.lab.critic_io as critic_io
    importlib.reload(critic_io)
    import openharness.lab.trial_evidence as trial_evidence
    importlib.reload(trial_evidence)
    import openharness.lab.gemini as gemini
    importlib.reload(gemini)

    trial = _trial_dir(tmp_path)
    binary = tmp_path / "gemini"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    def fake_which(name: str) -> str | None:
        return str(binary) if name == "gemini" else None

    payload = {
        "schema_version": 1,
        "task_summary": "Fix a bug.",
        "agent_strategy": "Ran tests twice.",
        "key_actions": ["turn 2: ran pytest"],
        "outcome": "failed",
        "root_cause": "The agent repeated pytest without editing.",
        "success_factor": None,
        "anti_patterns": ["repeated_failed_command"],
        "components_active": ["loop-guard"],
        "task_features": ["python-tests"],
        "surprising_observations": [],
        "confidence": 0.9,
    }

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"response": json.dumps(payload)}),
            stderr="",
        )

    monkeypatch.setattr(gemini.shutil, "which", fake_which)
    monkeypatch.setattr(gemini.subprocess, "run", fake_run)

    result = gemini.run_trial_critic(
        trial,
        cfg=gemini.GeminiConfig(binary=str(binary), cwd=repo),
    )

    assert result.ok is True
    assert result.payload is not None
    written = critic_io.read_trial_critique(trial)
    assert written is not None
    assert written["outcome"] == "failed"
    assert written["provenance"]["critic_model"] == gemini.DEFAULT_TRIAL_MODEL


def test_gemini_trial_critic_loads_repo_dotenv_for_worktree_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    (repo / ".agents" / "skills" / "trial-critic").mkdir(parents=True)
    (repo / ".agents" / "skills" / "trial-critic" / "SKILL.md").write_text(
        "---\nname: trial-critic\n---\n"
    )
    (repo / "runs" / "lab").mkdir(parents=True)
    (repo / ".env").write_text("GOOGLE_API_KEY=from-google\n", encoding="utf-8")
    worktree.mkdir()
    (worktree / ".agents" / "skills" / "trial-critic").mkdir(parents=True)
    (worktree / ".agents" / "skills" / "trial-critic" / "SKILL.md").write_text(
        "---\nname: trial-critic\n---\n"
    )
    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    import openharness.lab.paths as paths
    importlib.reload(paths)
    import openharness.lab.codex as codex
    importlib.reload(codex)
    import openharness.lab.critic_io as critic_io
    importlib.reload(critic_io)
    import openharness.lab.trial_evidence as trial_evidence
    importlib.reload(trial_evidence)
    import openharness.lab.gemini as gemini
    importlib.reload(gemini)

    trial = _trial_dir(tmp_path)
    binary = tmp_path / "gemini"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    captured_env: dict[str, str] = {}

    payload = {
        "schema_version": 1,
        "task_summary": "Fix a bug.",
        "agent_strategy": "Used the digest.",
        "key_actions": ["turn 1: inspected evidence"],
        "outcome": "passed",
        "root_cause": None,
        "success_factor": "The agent completed the task.",
        "anti_patterns": [],
        "components_active": [],
        "task_features": ["python-tests"],
        "surprising_observations": [],
        "confidence": 0.9,
    }

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_env.update(kwargs["env"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"response": json.dumps(payload)}),
            stderr="",
        )

    monkeypatch.setattr(gemini.shutil, "which", lambda _name: str(binary))
    monkeypatch.setattr(gemini.subprocess, "run", fake_run)

    result = gemini.run_trial_critic(
        trial,
        cfg=gemini.GeminiConfig(binary=str(binary), cwd=worktree),
        persist=False,
    )

    assert result.ok is True
    assert captured_env["GOOGLE_API_KEY"] == "from-google"
    assert captured_env["GEMINI_API_KEY"] == "from-google"


def test_gemini_invalid_payload_fails_without_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".agents" / "skills" / "trial-critic").mkdir(parents=True)
    (repo / ".agents" / "skills" / "trial-critic" / "SKILL.md").write_text(
        "---\nname: trial-critic\n---\n"
    )
    (repo / "runs" / "lab").mkdir(parents=True)
    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))

    import openharness.lab.paths as paths
    importlib.reload(paths)
    import openharness.lab.codex as codex
    importlib.reload(codex)
    import openharness.lab.critic_io as critic_io
    importlib.reload(critic_io)
    import openharness.lab.trial_evidence as trial_evidence
    importlib.reload(trial_evidence)
    import openharness.lab.gemini as gemini
    importlib.reload(gemini)

    trial = _trial_dir(tmp_path)
    binary = tmp_path / "gemini"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setattr(gemini.shutil, "which", lambda _name: str(binary))
    monkeypatch.setattr(
        gemini.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"response": "{}"}), stderr=""
        ),
    )

    result = gemini.run_trial_critic(
        trial,
        cfg=gemini.GeminiConfig(binary=str(binary), cwd=repo),
    )

    assert result.ok is False
    assert result.notes == "invalid_schema"
    assert critic_io.read_trial_critique(trial) is None
