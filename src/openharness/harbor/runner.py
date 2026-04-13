"""Python helpers for running Harbor jobs with installed Harbor CLI tools."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from openharness.config.paths import get_project_runs_dir
from openharness.harbor.specs import (
    HarborEnvironmentSpec,
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborRunResult,
    HarborTaskSpec,
    HarborToolSpec,
)
from openharness.runs.context import RunContext


def current_harbor_version(executable: str = "harbor") -> str | None:
    """Return the installed Harbor CLI version, if available."""
    resolved = shutil.which(executable)
    if resolved is None:
        return None
    result = subprocess.run(
        [resolved, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


def build_harbor_install_command(spec: HarborToolSpec) -> list[str]:
    """Build the UV command needed to install the pinned Harbor tool."""
    command = [
        spec.uv_executable,
        "tool",
        "install",
        "--force",
        f"harbor=={spec.version}",
    ]
    if spec.editable_openharness_dir is not None:
        command.extend(
            ["--with-editable", str(spec.editable_openharness_dir.expanduser().resolve())]
        )
    return command


def ensure_harbor_tool(spec: HarborToolSpec) -> str:
    """Install or repair the Harbor CLI tool to the requested pinned version."""
    resolved = shutil.which(spec.executable)
    if resolved is not None and current_harbor_version(spec.executable) == spec.version:
        return resolved

    subprocess.run(build_harbor_install_command(spec), check=True)

    resolved = shutil.which(spec.executable)
    if resolved is None:
        raise RuntimeError(f"Harbor executable was not found after installation: {spec.executable}")
    return resolved


def build_harbor_run_command(spec: HarborJobSpec) -> list[str]:
    """Translate the OpenHarness Harbor job spec into Harbor CLI arguments."""
    spec.task.validate()

    command = [
        spec.tool.executable,
        "run",
    ]
    if spec.yes:
        command.append("--yes")
    if spec.debug:
        command.append("--debug")
    if spec.quiet:
        command.append("--quiet")
    if spec.env_file is not None:
        command.extend(["--env-file", str(spec.env_file.expanduser().resolve())])
    if spec.n_attempts is not None:
        command.extend(["--n-attempts", str(spec.n_attempts)])
    if spec.n_concurrent_trials is not None:
        command.extend(["--n-concurrent", str(spec.n_concurrent_trials)])

    command.extend(["--job-name", spec.job_name])
    command.extend(["--jobs-dir", str(spec.jobs_dir.expanduser().resolve())])
    command.extend(["--agent-import-path", spec.agent.import_path])

    if spec.agent.model is not None:
        command.extend(["--model", spec.agent.model])

    _append_key_value_args(command, "--agent-kwarg", spec.agent.harbor_kwargs())
    _append_string_env_args(command, "--agent-env", _build_agent_env(spec))
    _append_environment_args(command, spec.environment)
    _append_task_args(command, spec.task)
    command.extend(spec.extra_args)
    return command


def run_harbor_job(spec: HarborJobSpec) -> HarborRunResult:
    """Ensure Harbor is installed, run the Harbor job, and return result metadata."""
    executable = ensure_harbor_tool(spec.tool)
    resolved_jobs_dir = spec.jobs_dir.expanduser().resolve()
    resolved_jobs_dir.mkdir(parents=True, exist_ok=True)
    resolved_job_name = resolve_harbor_job_name(spec, jobs_dir=resolved_jobs_dir)
    run_base_cwd = spec.run_cwd.expanduser().resolve() if spec.run_cwd is not None else resolved_jobs_dir
    run_context = RunContext.create(
        run_base_cwd,
        interface="harbor_job",
        run_id=resolved_job_name,
        metadata={
            "harbor_jobs_dir": str(resolved_jobs_dir),
        },
    )
    run_context.start(
        metadata={
            "job_name": resolved_job_name,
            "harbor_jobs_dir": str(resolved_jobs_dir),
        }
    )

    command = build_harbor_run_command(
        replace(
            spec,
            jobs_dir=resolved_jobs_dir,
            job_name=resolved_job_name,
        )
    )
    command[0] = executable
    try:
        subprocess.run(command, check=True)
    except Exception as exc:
        run_context.finish(
            status="failed",
            error=str(exc),
            metadata={"command": command},
        )
        raise

    run_context.save_manifest()

    return HarborRunResult(
        command=tuple(command),
        job_name=resolved_job_name,
        jobs_dir=resolved_jobs_dir,
        result_path=resolved_jobs_dir / resolved_job_name / "result.json",
    )


def resolve_harbor_job_name(spec: HarborJobSpec, *, jobs_dir: Path | None = None) -> str:
    """Resolve the final Harbor job name after applying the collision policy."""
    resolved_jobs_dir = jobs_dir or spec.jobs_dir.expanduser().resolve()
    job_dir = resolved_jobs_dir / spec.job_name

    if not job_dir.exists():
        return spec.job_name

    if spec.existing_job_policy is HarborExistingJobPolicy.RESUME:
        return spec.job_name

    if spec.existing_job_policy is HarborExistingJobPolicy.UNIQUE:
        return _next_available_job_name(resolved_jobs_dir, spec.job_name)

    raise FileExistsError(
        f"Job directory already exists: {job_dir}. "
        "Use a different job_name, set existing_job_policy='unique', "
        "or set existing_job_policy='resume' if you intentionally want Harbor to resume it."
    )


def _append_environment_args(command: list[str], spec: HarborEnvironmentSpec) -> None:
    if spec.type is not None:
        command.extend(["--env", spec.type])
    if spec.import_path is not None:
        command.extend(["--environment-import-path", spec.import_path])
    if spec.force_build is not None:
        command.append("--force-build" if spec.force_build else "--no-force-build")
    if spec.delete is not None:
        command.append("--delete" if spec.delete else "--no-delete")
    if spec.override_cpus is not None:
        command.extend(["--override-cpus", str(spec.override_cpus)])
    if spec.override_memory_mb is not None:
        command.extend(["--override-memory-mb", str(spec.override_memory_mb)])
    if spec.override_storage_mb is not None:
        command.extend(["--override-storage-mb", str(spec.override_storage_mb)])
    if spec.override_gpus is not None:
        command.extend(["--override-gpus", str(spec.override_gpus)])
    if spec.mounts_json is not None:
        command.extend(["--mounts-json", spec.mounts_json])
    _append_key_value_args(command, "--environment-kwarg", spec.kwargs)


def _append_task_args(command: list[str], spec: HarborTaskSpec) -> None:
    if spec.path is not None:
        command.extend(["--path", str(spec.path.expanduser().resolve())])
    if spec.registry_task is not None:
        command.extend(["--task", spec.registry_task])
    if spec.dataset is not None:
        command.extend(["--dataset", spec.dataset])
    if spec.task_git_url is not None:
        command.extend(["--task-git-url", spec.task_git_url])
    if spec.task_git_commit_id is not None:
        command.extend(["--task-git-commit", spec.task_git_commit_id])
    if spec.registry_url is not None:
        command.extend(["--registry-url", spec.registry_url])
    if spec.registry_path is not None:
        command.extend(["--registry-path", str(spec.registry_path.expanduser().resolve())])
    for task_name in spec.include_task_names:
        command.extend(["--include-task-name", task_name])
    for task_name in spec.exclude_task_names:
        command.extend(["--exclude-task-name", task_name])
    if spec.n_tasks is not None:
        command.extend(["--n-tasks", str(spec.n_tasks)])


def _append_key_value_args(command: list[str], flag: str, values: dict[str, Any]) -> None:
    for key, value in values.items():
        command.extend([flag, f"{key}={json.dumps(value)}"])


def _append_string_env_args(command: list[str], flag: str, values: dict[str, str]) -> None:
    for key, value in values.items():
        command.extend([flag, f"{key}={value}"])


def _next_available_job_name(jobs_dir: Path, base_name: str) -> str:
    suffix = 2
    while True:
        candidate = f"{base_name}-{suffix}"
        if not (jobs_dir / candidate).exists():
            return candidate
        suffix += 1


def _build_agent_env(spec: HarborJobSpec) -> dict[str, str]:
    base_cwd = spec.run_cwd.expanduser().resolve() if spec.run_cwd is not None else spec.jobs_dir.expanduser().resolve()
    run_root = get_project_runs_dir(base_cwd) / spec.job_name
    env = dict(spec.agent.env)
    env["OPENHARNESS_RUN_ID"] = spec.job_name
    env["OPENHARNESS_RUN_ROOT"] = str(run_root)
    return env
