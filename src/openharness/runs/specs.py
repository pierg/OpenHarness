"""Programmatic run specifications."""

from __future__ import annotations

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
    """Common metadata returned by run launch helpers."""

    run_id: str
    run_dir: Path
    manifest_path: Path
    trace_id: str | None = None
    trace_url: str | None = None
    result_path: Path | None = None
    metrics_path: Path | None = None
    external_result_path: Path | None = None
