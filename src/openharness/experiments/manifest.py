"""Experiment manifest and outcome models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from openharness.experiments.paths import RelPath


class LegStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"  # resume: already complete
    DRY_RUN = "dry_run"


class TrialRecord(BaseModel):
    trial_id: str
    task_name: str
    trial_dir: RelPath  # relative to experiment root
    score: float | None
    passed: bool
    error: str | None  # short
    traceback: str | None  # full (when applicable)
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    duration_sec: float | None
    agent_duration_sec: float | None
    env_setup_duration_sec: float | None
    verifier_duration_sec: float | None
    trace_id: str | None
    trace_url: str | None

    model_config = ConfigDict(extra="forbid", frozen=True)


class LegRecord(BaseModel):
    leg_id: str
    agent_id: str
    status: LegStatus
    started_at: datetime | None
    finished_at: datetime | None
    duration_sec: float | None
    harbor_dir: RelPath | None  # "legs/<leg_id>/harbor" - None for local
    harbor_result_path: RelPath | None  # "legs/<leg_id>/harbor/result.json"
    agent_config_path: RelPath | None  # "legs/<leg_id>/agent.resolved.yaml"
    trials: tuple[TrialRecord, ...] = ()
    error: str | None = None
    traceback: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class Reproducibility(BaseModel):
    git_sha: str | None
    git_dirty: bool
    harbor_version: str | None
    openharness_version: str
    python_version: str
    hostname: str  # informational only; not used for path resolution

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentManifest(BaseModel):
    schema_version: Literal[1] = 1
    experiment_id: str
    instance_id: str
    dataset: str
    spec_path: RelPath  # "config.source.yaml"
    resolved_spec_path: RelPath  # "config.resolved.yaml"
    created_at: datetime
    updated_at: datetime
    reproducibility: Reproducibility
    legs: tuple[LegRecord, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)
