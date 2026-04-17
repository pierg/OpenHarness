"""Programmatic run specifications."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openharness.api.client import SupportsStreamingMessages
from openharness.harbor.specs import HarborJobSpec


@dataclass(frozen=True)
class AgentSpec:
    """Agent selection and override knobs for a run."""

    name: str = "default"
    model: str | None = None
    max_turns: int | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class InlineTaskSpec:
    """A simple task payload for local and Harbor agent launches."""

    instruction: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalAgentRunSpec:
    """Top-level spec for launching a local OpenHarness agent run."""

    cwd: Path
    task: InlineTaskSpec
    agent: AgentSpec = field(default_factory=AgentSpec)
    run_id: str | None = None
    run_cwd: Path | None = None
    api_client: SupportsStreamingMessages | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarborAgentRunSpec:
    """Top-level spec for launching a Harbor-backed OpenHarness run."""

    cwd: Path
    job: HarborJobSpec
    run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunLaunchResult:
    """Metadata returned by local (single-task) run launch helpers."""

    run_id: str
    run_dir: Path
    manifest_path: Path
    trace_id: str | None = None
    trace_url: str | None = None
    result_path: Path | None = None
    metrics_path: Path | None = None

    @property
    def artifact_paths(self) -> dict[str, Path]:
        return {
            "manifest": self.manifest_path,
            "messages": self.run_dir / "messages.jsonl",
            "events": self.run_dir / "events.jsonl",
            "results": self.run_dir / "results.json",
            "metrics": self.run_dir / "metrics.json",
        }


@dataclass(frozen=True)
class TrialResult:
    """One agent execution on one task inside a Harbor job."""

    trial_id: str
    task_name: str
    trial_dir: Path
    score: float | None = None
    trace_id: str | None = None
    trace_url: str | None = None
    error: str | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    model: str | None = None

    duration_sec: float | None = None
    agent_duration_sec: float | None = None
    env_setup_duration_sec: float | None = None
    verifier_duration_sec: float | None = None

    @property
    def passed(self) -> bool:
        return self.score is not None and self.score > 0

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @property
    def artifact_paths(self) -> dict[str, Path]:
        return {
            "manifest": self.trial_dir / "run.json",
            "messages": self.trial_dir / "messages.jsonl",
            "events": self.trial_dir / "events.jsonl",
            "results": self.trial_dir / "results.json",
            "metrics": self.trial_dir / "metrics.json",
            "trajectory": self.trial_dir / "agent" / "trajectory.json",
        }


@dataclass(frozen=True)
class HarborJobResult:
    """Aggregated result of a Harbor job (one or more trials)."""

    job_id: str
    job_dir: Path
    harbor_result_path: Path
    trials: list[TrialResult] = field(default_factory=list)

    @property
    def mean_score(self) -> float | None:
        if not self.harbor_result_path.exists():
            return None
        try:
            data = json.loads(self.harbor_result_path.read_text(encoding="utf-8"))
            evals = data.get("stats", {}).get("evals", {})
            for value in evals.values():
                metrics = value.get("metrics", [])
                if metrics:
                    return metrics[0].get("mean")
        except Exception:
            pass
        return None

    @property
    def n_passed(self) -> int:
        return sum(1 for t in self.trials if t.passed)

    @property
    def n_errors(self) -> int:
        return sum(1 for t in self.trials if t.error is not None)
