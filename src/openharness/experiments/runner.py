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
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
)
from openharness.runs import HarborAgentRunSpec, HarborJobResult, run_harbor_agent

from openharness.experiments.specs import (
    ExperimentJob,
    ExperimentConfig,
    expand_experiment_jobs,
)


@dataclass(frozen=True)
class ExperimentJobRecord:
    """Manifest record for one expanded experiment job."""

    job_id: str
    experiment_instance_id: str
    agent_id: str
    dataset: str
    openharness_run_id: str | None
    harbor_result_path: str | None
    status: str
    config: dict[str, Any]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "experiment_instance_id": self.experiment_instance_id,
            "agent_id": self.agent_id,
            "dataset": self.dataset,
            "openharness_run_id": self.openharness_run_id,
            "harbor_result_path": self.harbor_result_path,
            "status": self.status,
            "config": self.config,
            "error": self.error,
        }


def resolve_agent_config_for_experiment(
    agent_id: str,
    config: ExperimentConfig,
    *,
    cwd: str | Path | None = None,
) -> AgentConfig:
    """Resolve a catalog agent id and apply runtime overrides in memory."""
    item = get_catalog_agent_config(agent_id, cwd)
    if item is None:
        raise KeyError(f"Unknown agent config id: {agent_id}")
    return _apply_agent_runtime_overrides(item.config, config)


def build_harbor_run_spec(
    job: ExperimentJob,
    *,
    cwd: str | Path,
    jobs_dir: Path | None = None,
    env: dict[str, str] | None = None,
    resume: bool = False,
) -> HarborAgentRunSpec:
    """Translate an expanded experiment job into a Harbor run spec."""
    root = Path(cwd).expanduser().resolve()
    config = resolve_agent_config_for_experiment(job.agent_id, job.config, cwd=root)
    config_yaml = _dump_agent_config_yaml(config)

    resolved_jobs_dir = jobs_dir or (
        get_project_runs_dir(root) / job.harbor_run_id / "harbor_jobs"
    )
    job_spec = HarborJobSpec(
        tool=HarborToolSpec(editable_openharness_dir=root),
        agent=OpenHarnessHarborAgentSpec(
            agent_name=config.name,
            model=job.config.model or config.model,
            max_turns=job.config.max_turns or config.max_turns,
            max_tokens=job.config.max_tokens or config.max_tokens,
            agent_config_yaml=config_yaml,
            env=dict(env or {}),
        ),
        task=HarborTaskSpec(
            dataset=job.dataset,
            include_task_names=job.config.include_tasks or (),
            exclude_task_names=job.config.exclude_tasks or (),
            n_tasks=job.config.n_tasks,
        ),
        environment=HarborEnvironmentSpec(type="docker"),
        jobs_dir=resolved_jobs_dir,
        n_attempts=job.config.n_attempts,
        n_concurrent_trials=job.config.n_concurrent,
        existing_job_policy=(
            HarborExistingJobPolicy.RESUME if resume else HarborExistingJobPolicy.ERROR
        ),
        metadata={
            "experiment_id": job.experiment_id,
            "experiment_instance_id": job.experiment_instance_id,
            "agent_id": job.agent_id,
            "job_id": job.job_id,
            "dataset": job.dataset,
        },
    )
    return HarborAgentRunSpec(cwd=root, job=job_spec, run_id=job.harbor_run_id)


def run_experiment(
    config: ExperimentConfig,
    *,
    cwd: str | Path,
    manifest_path: str | Path | None = None,
    env: dict[str, str] | None = None,
    experiment_instance_id: str | None = None,
    dry_run: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    """Run or dry-run all jobs in an experiment spec."""
    resolved_instance_id = experiment_instance_id or config.id or "experiment"
    jobs = expand_experiment_jobs(
        config,
        experiment_instance_id=resolved_instance_id,
    )
    records: list[ExperimentJobRecord] = (
        _load_existing_records(manifest_path) if resume and manifest_path is not None else []
    )
    records = [
        record
        for record in records
        if record.experiment_instance_id == resolved_instance_id
    ]
    for job in jobs:
        run_spec = build_harbor_run_spec(job, cwd=cwd, env=env, resume=resume)
        result_path = _expected_harbor_result_path(run_spec)
        if dry_run:
            records = _upsert_record(
                records,
                _record_for_job(job, run_spec.run_id, str(result_path), "dry_run"),
            )
            _write_manifest_if_requested(config, resolved_instance_id, records, manifest_path)
            continue

        if resume and _harbor_job_complete(result_path):
            records = _upsert_record(
                records,
                _record_for_job(job, run_spec.run_id, str(result_path), "succeeded"),
            )
            _write_manifest_if_requested(config, resolved_instance_id, records, manifest_path)
            continue

        records = _upsert_record(
            records,
            _record_for_job(job, run_spec.run_id, str(result_path), "running"),
        )
        _write_manifest_if_requested(config, resolved_instance_id, records, manifest_path)
        try:
            result = run_harbor_agent(run_spec)
            records = _upsert_record(records, _record_for_result(job, result, "succeeded"))
        except KeyboardInterrupt:
            records = _upsert_record(
                records,
                _record_for_job(
                    job,
                    run_spec.run_id,
                    str(result_path),
                    "interrupted",
                    error="KeyboardInterrupt",
                ),
            )
            _write_manifest_if_requested(config, resolved_instance_id, records, manifest_path)
            raise
        except Exception as exc:
            records = _upsert_record(
                records,
                _record_for_job(job, run_spec.run_id, str(result_path), "failed", error=str(exc)),
            )
            raise
        finally:
            _write_manifest_if_requested(config, resolved_instance_id, records, manifest_path)

    manifest = {
        "experiment_id": config.id,
        "experiment_instance_id": resolved_instance_id,
        "dataset": config.dataset,
        "jobs": [record.as_dict() for record in records],
    }
    if manifest_path is not None:
        write_experiment_manifest(manifest, manifest_path)
    return manifest


def write_experiment_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _write_manifest_if_requested(
    config: ExperimentConfig,
    experiment_instance_id: str,
    records: list[ExperimentJobRecord],
    manifest_path: str | Path | None,
) -> None:
    if manifest_path is None:
        return
    write_experiment_manifest(
        {
            "experiment_id": config.id,
            "experiment_instance_id": experiment_instance_id,
            "dataset": config.dataset,
            "jobs": [record.as_dict() for record in records],
        },
        manifest_path,
    )


def _load_existing_records(manifest_path: str | Path | None) -> list[ExperimentJobRecord]:
    if manifest_path is None:
        return []
    path = Path(manifest_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records: list[ExperimentJobRecord] = []
    for raw in data.get("jobs", []):
        if not isinstance(raw, dict):
            continue
        try:
            records.append(
                ExperimentJobRecord(
                    job_id=raw["job_id"],
                    experiment_instance_id=raw["experiment_instance_id"],
                    agent_id=raw["agent_id"],
                    dataset=raw["dataset"],
                    openharness_run_id=raw.get("openharness_run_id"),
                    harbor_result_path=raw.get("harbor_result_path"),
                    status=raw["status"],
                    config=raw.get("config") or {},
                    error=raw.get("error"),
                )
            )
        except KeyError:
            continue
    return records


def _upsert_record(
    records: list[ExperimentJobRecord],
    record: ExperimentJobRecord,
) -> list[ExperimentJobRecord]:
    updated: list[ExperimentJobRecord] = []
    replaced = False
    for existing in records:
        if existing.job_id == record.job_id:
            updated.append(record)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(record)
    return updated


def _expected_harbor_result_path(spec: HarborAgentRunSpec) -> Path:
    return spec.job.jobs_dir.expanduser().resolve() / spec.run_id / "result.json"


def _harbor_job_complete(result_path: Path) -> bool:
    if not result_path.exists():
        return False
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    expected_trials = data.get("n_total_trials")
    if not isinstance(expected_trials, int):
        return False

    completed_trials = sum(
        1
        for trial_dir in result_path.parent.iterdir()
        if trial_dir.is_dir() and (trial_dir / "result.json").exists()
    )
    return completed_trials >= expected_trials


def _apply_agent_runtime_overrides(
    agent_config: AgentConfig,
    experiment_config: ExperimentConfig,
) -> AgentConfig:
    update: dict[str, Any] = {}
    if experiment_config.model is not None:
        update["model"] = experiment_config.model
    if experiment_config.max_turns is not None:
        update["max_turns"] = experiment_config.max_turns
    if experiment_config.max_tokens is not None:
        update["max_tokens"] = experiment_config.max_tokens
    if agent_config.subagents:
        update["subagents"] = {
            name: _apply_agent_runtime_overrides(subagent, experiment_config)
            for name, subagent in agent_config.subagents.items()
        }
    return agent_config.model_copy(update=update)


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
        experiment_instance_id=job.experiment_instance_id,
        agent_id=job.agent_id,
        dataset=job.dataset,
        openharness_run_id=openharness_run_id,
        harbor_result_path=harbor_result_path,
        status=status,
        config=job.config.model_dump(mode="json"),
        error=error,
    )
