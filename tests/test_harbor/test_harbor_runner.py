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
from openharness.observability import TraceIdentity
from openharness.runs import HarborAgentRunSpec, run_harbor_agent


def test_build_harbor_install_command_with_editable_openharness_dir() -> None:
    command = build_harbor_install_command(
        HarborToolSpec(version="0.3.0", editable_openharness_dir=Path("/tmp/openharness"))
    )
    assert command == [
        "uv", "tool", "install", "--force", "harbor==0.3.0",
        "--with-editable", str(Path("/tmp/openharness").resolve()),
    ]


def test_build_harbor_run_command_maps_agent_task_and_environment_specs() -> None:
    command = build_harbor_run_command(HarborJobSpec(
        job_name="job-1",
        jobs_dir=Path("/tmp/jobs"),
        tool=HarborToolSpec(version="0.3.0"),
        agent=OpenHarnessHarborAgentSpec(
            agent_name="react_example",
            model="gemini-2.5-flash-lite",
            agent_config_yaml="name: react_example\narchitecture: simple\nmodel: gemini-2.5-flash-lite\n",
        ),
        task=HarborTaskSpec(path=Path("/tmp/task")),
        environment=HarborEnvironmentSpec(type="docker", override_cpus=2),
        n_concurrent_trials=1,
    ))

    assert command[:4] == ["harbor", "run", "--yes", "--n-concurrent"]
    assert "--agent-import-path" in command
    assert "openharness.harbor:OpenHarnessHarborAgent" in command
    assert "--model" in command and "gemini-2.5-flash-lite" in command
    assert "--path" in command and str(Path("/tmp/task").resolve()) in command
    assert "--env" in command and "docker" in command
    assert "--override-cpus" in command
    assert "--agent-kwarg" in command
    assert 'agent_name="react_example"' in command
    assert 'remote_cwd="/app"' in command
    assert any(item.startswith("agent_config_yaml=") for item in command)
    assert 'run_id="job-1"' in command
    assert f"run_root={json.dumps(str(Path('/tmp/jobs').resolve() / 'runs' / 'job-1'))}" in command
    assert "--agent-env" not in command


def test_harbor_task_spec_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="Exactly one Harbor task source must be set"):
        HarborTaskSpec().validate()

    with pytest.raises(ValueError, match="Exactly one Harbor task source must be set"):
        HarborTaskSpec(path=Path("/tmp/task"), dataset="demo").validate()


def test_resolve_harbor_job_name_unique_suffix(tmp_path: Path) -> None:
    (tmp_path / "demo-job").mkdir()
    (tmp_path / "demo-job-2").mkdir()

    job_name = resolve_harbor_job_name(HarborJobSpec(
        job_name="demo-job",
        jobs_dir=tmp_path,
        tool=HarborToolSpec(version="0.3.0"),
        agent=OpenHarnessHarborAgentSpec(model="gemini-2.5-flash-lite"),
        task=HarborTaskSpec(path=Path("/tmp/task")),
        environment=HarborEnvironmentSpec(type="docker"),
        existing_job_policy=HarborExistingJobPolicy.UNIQUE,
    ))

    assert job_name == "demo-job-3"


def test_resolve_harbor_job_name_errors_on_collision(tmp_path: Path) -> None:
    (tmp_path / "demo-job").mkdir()

    with pytest.raises(FileExistsError, match="existing_job_policy='unique'"):
        resolve_harbor_job_name(HarborJobSpec(
            job_name="demo-job",
            jobs_dir=tmp_path,
            tool=HarborToolSpec(version="0.3.0"),
            agent=OpenHarnessHarborAgentSpec(model="gemini-2.5-flash-lite"),
            task=HarborTaskSpec(path=Path("/tmp/task")),
            environment=HarborEnvironmentSpec(type="docker"),
        ))


def test_run_harbor_job_finishes_host_run_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_dir = tmp_path / "harbor_jobs"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    workspace_dir = tmp_path / "runs" / "job-1" / "workspace"
    workspace_dir.mkdir(parents=True)

    monkeypatch.setattr("openharness.harbor.runner.ensure_harbor_tool", lambda _spec: "harbor")
    monkeypatch.setattr(
        "openharness.harbor.runner.resolve_langfuse_trace_identity",
        lambda *, run_id: TraceIdentity(
            trace_id=f"trace-{run_id}",
            trace_url=f"http://langfuse.test/traces/trace-{run_id}",
        ),
    )

    def fake_run(command, *, check):
        assert check is True
        assert command[0] == "harbor"
        result_path = jobs_dir / "job-1" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("{}", encoding="utf-8")
        trial_result_path = jobs_dir / "job-1" / "workspace__abc" / "result.json"
        trial_result_path.parent.mkdir(parents=True, exist_ok=True)
        trial_result_path.write_text(
            json.dumps({
                "agent_result": {
                    "metadata": {
                        "trace_id": "trace-trial",
                        "trace_url": "http://langfuse.test/traces/trace-trial",
                    }
                }
            }),
            encoding="utf-8",
        )

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

    run_dir = tmp_path / "runs" / "job-1"
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))

    assert result.result_path == jobs_dir.resolve() / "job-1" / "result.json"
    assert result.trace_id == "trace-trial"
    assert result.trace_url == "http://langfuse.test/traces/trace-trial"
    assert manifest["status"] == "completed"
    assert manifest["trace_id"] == "trace-trial"
    assert manifest["trace_url"] == "http://langfuse.test/traces/trace-trial"
    assert manifest["artifacts"]["workspace_dir"] == str(workspace_dir.resolve())
    assert manifest["metadata"]["example"] == "harbor_fix_bug"
    assert manifest["metadata"]["model"] == "gemini-2.5-flash-lite"
    assert manifest["metadata"]["trace_url"] == "http://langfuse.test/traces/trace-trial"
    assert results["job_name"] == "job-1"
    assert results["harbor_result_exists"] is True
    assert results["trace_id"] == "trace-trial"
    assert results["trace_url"] == "http://langfuse.test/traces/trace-trial"
    assert metrics["elapsed_seconds"] >= 0


def test_run_harbor_agent_generates_run_id_when_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_run_harbor_job(spec):
        captured["job_name"] = spec.job_name
        captured["run_cwd"] = spec.run_cwd
        captured["metadata"] = spec.metadata
        result_path = tmp_path / "harbor_jobs" / spec.job_name / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("{}", encoding="utf-8")
        trial_result_path = result_path.parent / "workspace__abc" / "result.json"
        trial_result_path.parent.mkdir()
        trial_result_path.write_text(
            json.dumps({
                "agent_result": {
                    "metadata": {
                        "trace_id": "trace-from-trial",
                        "trace_url": "http://langfuse.test/traces/trace-from-trial",
                    }
                }
            }),
            encoding="utf-8",
        )

        class _Result:
            pass

        result = _Result()
        result.result_path = result_path
        return result

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

    assert result.run_id.startswith("run-")
    assert captured["job_name"] == result.run_id
    assert captured["run_cwd"] == tmp_path.resolve()
    assert captured["metadata"] == {"example": "harbor_fix_bug"}
    assert result.run_dir == tmp_path / "runs" / result.run_id
    assert result.trace_id == "trace-from-trial"
    assert result.trace_url == "http://langfuse.test/traces/trace-from-trial"
