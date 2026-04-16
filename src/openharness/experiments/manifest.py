"""Experiment manifest and outcome models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openharness.experiments.paths import RelPath


SCHEMA_VERSION = 2


class LegStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


class LegResultStatus(str, Enum):
    """Summary of trial outcomes within a leg (independent of backend success)."""

    ALL_PASSED = "all_passed"
    PARTIAL = "partial"
    ALL_FAILED = "all_failed"
    ALL_ERRORED = "all_errored"
    NO_TRIALS = "no_trials"


class TrialErrorPhase(str, Enum):
    ENV_SETUP = "env_setup"
    AGENT = "agent"
    VERIFIER = "verifier"
    UNKNOWN = "unknown"


class TrialError(BaseModel):
    """Structured trial error payload."""

    exception_type: str | None = None
    message: str
    phase: TrialErrorPhase = TrialErrorPhase.UNKNOWN
    occurred_at: datetime | None = None
    traceback: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class TrialRecord(BaseModel):
    trial_id: str
    task_name: str
    trial_dir: RelPath
    score: float | None = None
    passed: bool = False
    error: TrialError | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    duration_sec: float | None = None
    agent_duration_sec: float | None = None
    env_setup_duration_sec: float | None = None
    verifier_duration_sec: float | None = None
    trace_id: str | None = None
    trace_url: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class LegAggregate(BaseModel):
    """Per-leg aggregated trial statistics."""

    n_trials: int
    n_passed: int
    n_failed: int
    n_errored: int
    n_errored_by_phase: dict[str, int] = Field(default_factory=dict)
    mean_score: float | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    model_config = ConfigDict(extra="forbid", frozen=True)


class LegRecord(BaseModel):
    leg_id: str
    agent_id: str
    status: LegStatus
    result_status: LegResultStatus | None = None
    started_at: datetime | None
    finished_at: datetime | None
    duration_sec: float | None
    harbor_dir: RelPath | None
    harbor_result_path: RelPath | None
    agent_config_path: RelPath | None
    trials: tuple[TrialRecord, ...] = ()
    aggregate: LegAggregate | None = None
    error: str | None = None
    traceback: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class Reproducibility(BaseModel):
    git_sha: str | None
    git_dirty: bool
    harbor_version: str | None
    openharness_version: str
    python_version: str
    hostname: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentManifest(BaseModel):
    schema_version: Literal[2] = 2
    experiment_id: str
    instance_id: str
    dataset: str
    spec_path: RelPath
    resolved_spec_path: RelPath
    created_at: datetime
    updated_at: datetime
    reproducibility: Reproducibility
    legs: tuple[LegRecord, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)
