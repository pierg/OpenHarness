"""Tests for Harbor runner helpers."""

from __future__ import annotations

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
)


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
    assert "--agent-env" in command
    assert "OPENHARNESS_RUN_ID=job-1" in command
    assert f"OPENHARNESS_RUN_ROOT={Path('/tmp/jobs').resolve() / 'runs' / 'job-1'}" in command


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
