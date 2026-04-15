"""Tests for Harbor runner helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.harbor import (
    HarborEnvironmentSpec,
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
    build_harbor_install_command,
    build_harbor_run_command,
    resolve_harbor_job_name,
    run_harbor_job,
)
from openharness.runs import HarborAgentRunSpec, run_harbor_agent


def test_build_harbor_install_command_with_editable_openharness_dir() -> None:
    command = build_harbor_install_command(
        HarborToolSpec(version="0.3.0", editable_openharness_dir=Path("/tmp/openharness"))
    )
    assert command == [
        "uv",
        "tool",
        "install",
        "--force",
        "harbor==0.3.0",
        "--with-editable",
        str(Path("/tmp/openharness").resolve()),
    ]


def test_build_harbor_run_command_maps_agent_task_and_environment_specs() -> None:
    command = build_harbor_run_command(
        HarborJobSpec(
            job_name="job-1",
            jobs_dir=Path("/tmp/jobs"),
            tool=HarborToolSpec(version="0.3.0"),
            agent=OpenHarnessHarborAgentSpec(
                agent_name="react",
                model="gemini-2.5-flash-lite",
                agent_config_yaml="name: react\narchitecture: simple\nmodel: gemini-2.5-flash-lite\n",
            ),
            task=HarborTaskSpec(path=Path("/tmp/task")),
            environment=HarborEnvironmentSpec(type="docker", override_cpus=2),
            n_concurrent_trials=1,
        )
    )

    assert command[:4] == ["harbor", "run", "--yes", "--n-concurrent"]
    assert "--agent-import-path" in command
    assert "openharness.harbor:OpenHarnessHarborAgent" in command
    assert "--model" in command and "gemini-2.5-flash-lite" in command
    assert "--path" in command and str(Path("/tmp/task").resolve()) in command
    assert "--env" in command and "docker" in command
    assert "--override-cpus" in command
    assert "--agent-kwarg" in command
    assert 'agent_name="react"' in command
    assert 'remote_cwd="/app"' in command
    assert any(item.startswith("agent_config_yaml=") for item in command)
    assert 'run_id="job-1"' in command
    assert any("run_root=" in arg and "job-1" in arg for arg in command)
    assert "--agent-env" not in command


def test_harbor_task_spec_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="Exactly one Harbor task source must be set"):
        HarborTaskSpec().validate()

    with pytest.raises(ValueError, match="Exactly one Harbor task source must be set"):
        HarborTaskSpec(path=Path("/tmp/task"), dataset="demo").validate()


def test_resolve_harbor_job_name_unique_suffix(tmp_path: Path) -> None:
    (tmp_path / "demo-job").mkdir()
    (tmp_path / "demo-job-2").mkdir()

    job_name = resolve_harbor_job_name(
        HarborJobSpec(
            job_name="demo-job",
            jobs_dir=tmp_path,
            tool=HarborToolSpec(version="0.3.0"),
            agent=OpenHarnessHarborAgentSpec(model="gemini-2.5-flash-lite"),
            task=HarborTaskSpec(path=Path("/tmp/task")),
            environment=HarborEnvironmentSpec(type="docker"),
            existing_job_policy=HarborExistingJobPolicy.UNIQUE,
        )
    )

    assert job_name == "demo-job-3"


def test_resolve_harbor_job_name_errors_on_collision(tmp_path: Path) -> None:
    (tmp_path / "demo-job").mkdir()

    with pytest.raises(FileExistsError, match="existing_job_policy='unique'"):
        resolve_harbor_job_name(
            HarborJobSpec(
                job_name="demo-job",
                jobs_dir=tmp_path,
                tool=HarborToolSpec(version="0.3.0"),
                agent=OpenHarnessHarborAgentSpec(model="gemini-2.5-flash-lite"),
                task=HarborTaskSpec(path=Path("/tmp/task")),
                environment=HarborEnvironmentSpec(type="docker"),
            )
        )


def test_run_harbor_job_returns_result_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_dir = tmp_path / "harbor_jobs"
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    monkeypatch.setattr("openharness.harbor.runner.ensure_harbor_tool", lambda _spec: "harbor")

    def fake_run(command, *, check):
        assert check is True
        assert command[0] == "harbor"
        result_path = jobs_dir / "job-1" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("openharness.harbor.runner.subprocess.run", fake_run)

    result = run_harbor_job(
        HarborJobSpec(
            job_name="job-1",
            jobs_dir=jobs_dir,
            run_cwd=tmp_path,
            tool=HarborToolSpec(version="0.3.0"),
            agent=OpenHarnessHarborAgentSpec(model="gemini-2.5-flash-lite"),
            task=HarborTaskSpec(path=task_dir),
            environment=HarborEnvironmentSpec(type="docker"),
            metadata={"example": "harbor_fix_bug"},
        )
    )

    assert result.result_path == jobs_dir.resolve() / "job-1" / "result.json"
    assert result.job_name == "job-1"


def test_run_harbor_agent_returns_job_result_with_trials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_run_harbor_job(spec):
        captured["job_name"] = spec.job_name
        captured["run_cwd"] = spec.run_cwd
        captured["metadata"] = spec.metadata

        job_result_dir = spec.jobs_dir.resolve() / spec.job_name
        job_result_dir.mkdir(parents=True, exist_ok=True)

        result_path = job_result_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "stats": {
                        "evals": {
                            "openharness__test": {
                                "metrics": [{"mean": 0.5}],
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        trial_dir = job_result_dir / "task-foo__abc123"
        trial_dir.mkdir()
        (trial_dir / "result.json").write_text(
            json.dumps(
                {
                    "agent_result": {
                        "n_input_tokens": 5000,
                        "n_output_tokens": 200,
                        "cost_usd": 0.01,
                        "metadata": {
                            "model": "gemini-2.5-flash",
                            "trace_id": "trace-trial-foo",
                            "trace_url": "http://langfuse.test/traces/trace-trial-foo",
                        },
                    },
                    "verifier_result": {"rewards": {"reward": 1.0}},
                    "started_at": "2026-04-14T10:00:00.000000Z",
                    "finished_at": "2026-04-14T10:01:00.000000Z",
                    "agent_execution": {
                        "started_at": "2026-04-14T10:00:10.000000Z",
                        "finished_at": "2026-04-14T10:00:50.000000Z",
                    },
                }
            ),
            encoding="utf-8",
        )
        (trial_dir / "run.json").write_text(
            json.dumps(
                {
                    "trace_id": "trace-trial-foo",
                    "trace_url": "http://langfuse.test/traces/trace-trial-foo",
                }
            ),
            encoding="utf-8",
        )

        trial_dir_2 = job_result_dir / "task-bar__xyz789"
        trial_dir_2.mkdir()
        (trial_dir_2 / "result.json").write_text(
            json.dumps(
                {
                    "agent_result": {
                        "n_input_tokens": 3000,
                        "n_output_tokens": 100,
                        "metadata": {
                            "model": "gemini-2.5-flash",
                            "trace_id": "trace-trial-bar",
                            "trace_url": "http://langfuse.test/traces/trace-trial-bar",
                        },
                    },
                    "verifier_result": {"rewards": {"reward": 0.0}},
                    "started_at": "2026-04-14T10:00:00.000000Z",
                    "finished_at": "2026-04-14T10:00:30.000000Z",
                }
            ),
            encoding="utf-8",
        )
        (trial_dir_2 / "run.json").write_text(
            json.dumps(
                {
                    "trace_id": "trace-trial-bar",
                    "trace_url": "http://langfuse.test/traces/trace-trial-bar",
                }
            ),
            encoding="utf-8",
        )

        from openharness.harbor.specs import HarborRunResult

        return HarborRunResult(
            command=("harbor", "run"),
            job_name=spec.job_name,
            jobs_dir=spec.jobs_dir.resolve(),
            result_path=result_path,
        )

    monkeypatch.setattr("openharness.runs.harbor.run_harbor_job", fake_run_harbor_job)

    result = run_harbor_agent(
        HarborAgentRunSpec(
            cwd=tmp_path,
            metadata={"example": "harbor_fix_bug"},
            job=HarborJobSpec(
                jobs_dir=tmp_path / "harbor_jobs",
                tool=HarborToolSpec(version="0.3.0"),
                agent=OpenHarnessHarborAgentSpec(model="gemini-2.5-flash-lite"),
                task=HarborTaskSpec(path=tmp_path / "task"),
                environment=HarborEnvironmentSpec(type="docker"),
            ),
        )
    )

    assert result.job_id.startswith("run-")
    assert captured["job_name"] == result.job_id
    assert captured["run_cwd"] == tmp_path.resolve()
    assert captured["metadata"] == {"example": "harbor_fix_bug"}

    assert len(result.trials) == 2

    bar = result.trials[0]
    assert bar.task_name == "task-bar"
    assert bar.score == 0.0
    assert bar.trace_id == "trace-trial-bar"
    assert not bar.passed
    assert bar.input_tokens == 3000
    assert bar.output_tokens == 100
    assert bar.total_tokens == 3100
    assert bar.model == "gemini-2.5-flash"
    assert bar.duration_sec == 30.0

    foo = result.trials[1]
    assert foo.task_name == "task-foo"
    assert foo.score == 1.0
    assert foo.trace_id == "trace-trial-foo"
    assert foo.passed
    assert foo.input_tokens == 5000
    assert foo.output_tokens == 200
    assert foo.cost_usd == 0.01
    assert foo.duration_sec == 60.0
    assert foo.agent_duration_sec == 40.0

    assert result.mean_score == 0.5
    assert result.n_passed == 1
