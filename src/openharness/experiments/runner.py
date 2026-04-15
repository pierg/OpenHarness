"""Experiment runner helpers for Harbor-backed benchmark jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openharness.agents.catalog import get_catalog_agent_config
from openharness.agents.config import AgentConfig
from openharness.config.paths import get_project_runs_dir
from openharness.harbor import (
    HarborEnvironmentSpec,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
)
from openharness.runs import HarborAgentRunSpec, HarborJobResult, run_harbor_agent

from openharness.experiments.specs import (
    ExperimentJob,
    ExperimentRuntimeOverrides,
    ExperimentSpec,
    expand_experiment_jobs,
)


@dataclass(frozen=True)
class ExperimentJobRecord:
    """Manifest record for one expanded experiment job."""

    job_id: str
    run_spec_id: str
    agent_id: str
    dataset: str
    openharness_run_id: str | None
    harbor_result_path: str | None
    status: str
    settings: dict[str, Any]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_spec_id": self.run_spec_id,
            "agent_id": self.agent_id,
            "dataset": self.dataset,
            "openharness_run_id": self.openharness_run_id,
            "harbor_result_path": self.harbor_result_path,
            "status": self.status,
            "settings": self.settings,
            "error": self.error,
        }


def resolve_agent_config_for_experiment(
    agent_id: str,
    overrides: ExperimentRuntimeOverrides,
    *,
    cwd: str | Path | None = None,
) -> AgentConfig:
    """Resolve a catalog agent id and apply runtime overrides in memory."""
    item = get_catalog_agent_config(agent_id, cwd)
    if item is None:
        raise KeyError(f"Unknown agent config id: {agent_id}")
    return _apply_agent_runtime_overrides(item.config, overrides)


def build_harbor_run_spec(
    job: ExperimentJob,
    *,
    cwd: str | Path,
    jobs_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> HarborAgentRunSpec:
    """Translate an expanded experiment job into a Harbor run spec."""
    root = Path(cwd).expanduser().resolve()
    config = resolve_agent_config_for_experiment(job.agent_id, job.settings, cwd=root)
    config_yaml = _dump_agent_config_yaml(config)

    resolved_jobs_dir = jobs_dir or (
        get_project_runs_dir(root) / job.harbor_run_id / "harbor_jobs"
    )
    job_spec = HarborJobSpec(
        tool=HarborToolSpec(editable_openharness_dir=root),
        agent=OpenHarnessHarborAgentSpec(
            agent_name=config.name,
            model=job.settings.model or config.model,
            max_turns=job.settings.max_turns or config.max_turns,
            max_tokens=job.settings.max_tokens or config.max_tokens,
            agent_config_yaml=config_yaml,
            env=dict(env or {}),
        ),
        task=HarborTaskSpec(
            dataset=job.dataset,
            include_task_names=job.settings.include_tasks or (),
            exclude_task_names=job.settings.exclude_tasks or (),
            n_tasks=job.settings.n_tasks,
        ),
        environment=HarborEnvironmentSpec(type="docker"),
        jobs_dir=resolved_jobs_dir,
        n_attempts=job.settings.n_attempts,
        n_concurrent_trials=job.settings.n_concurrent,
        metadata={
            "experiment_id": job.experiment_id,
            "run_id": job.run_id,
            "agent_id": job.agent_id,
            "job_id": job.job_id,
            "dataset": job.dataset,
        },
    )
    return HarborAgentRunSpec(cwd=root, job=job_spec, run_id=job.harbor_run_id)


def run_experiment(
    spec: ExperimentSpec,
    *,
    cwd: str | Path,
    manifest_path: str | Path | None = None,
    env: dict[str, str] | None = None,
    cli_overrides: ExperimentRuntimeOverrides | None = None,
    only_run_ids: set[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run or dry-run all jobs in an experiment spec."""
    jobs = [
        job
        for job in expand_experiment_jobs(spec, cli_overrides=cli_overrides)
        if only_run_ids is None or job.run_id in only_run_ids
    ]
    records: list[ExperimentJobRecord] = []
    for job in jobs:
        run_spec = build_harbor_run_spec(job, cwd=cwd, env=env)
        if dry_run:
            records.append(_record_for_job(job, run_spec.run_id, None, "dry_run"))
            continue
        try:
            result = run_harbor_agent(run_spec)
            records.append(_record_for_result(job, result, "succeeded"))
        except Exception as exc:
            records.append(_record_for_job(job, run_spec.run_id, None, "failed", error=str(exc)))
            raise
        finally:
            if manifest_path is not None:
                write_experiment_manifest(
                    {
                        "experiment_id": spec.id,
                        "dataset": spec.dataset,
                        "jobs": [record.as_dict() for record in records],
                    },
                    manifest_path,
                )

    manifest = {
        "experiment_id": spec.id,
        "dataset": spec.dataset,
        "jobs": [record.as_dict() for record in records],
    }
    if manifest_path is not None:
        write_experiment_manifest(manifest, manifest_path)
    return manifest


def write_experiment_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _apply_agent_runtime_overrides(
    config: AgentConfig,
    overrides: ExperimentRuntimeOverrides,
) -> AgentConfig:
    update: dict[str, Any] = {}
    if overrides.model is not None:
        update["model"] = overrides.model
    if overrides.max_turns is not None:
        update["max_turns"] = overrides.max_turns
    if overrides.max_tokens is not None:
        update["max_tokens"] = overrides.max_tokens
    if config.subagents:
        update["subagents"] = {
            name: _apply_agent_runtime_overrides(subagent, overrides)
            for name, subagent in config.subagents.items()
        }
    return config.model_copy(update=update)


def _dump_agent_config_yaml(config: AgentConfig) -> str:
    raw = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(raw, sort_keys=False)


def _record_for_result(
    job: ExperimentJob,
    result: HarborJobResult,
    status: str,
) -> ExperimentJobRecord:
    return _record_for_job(
        job,
        result.job_id,
        str(result.harbor_result_path),
        status,
    )


def _record_for_job(
    job: ExperimentJob,
    openharness_run_id: str | None,
    harbor_result_path: str | None,
    status: str,
    *,
    error: str | None = None,
) -> ExperimentJobRecord:
    return ExperimentJobRecord(
        job_id=job.job_id,
        run_spec_id=job.run_id,
        agent_id=job.agent_id,
        dataset=job.dataset,
        openharness_run_id=openharness_run_id,
        harbor_result_path=harbor_result_path,
        status=status,
        settings=job.settings.model_dump(mode="json"),
        error=error,
    )
