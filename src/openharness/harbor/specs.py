"""Programmatic specs for running OpenHarness agents via Harbor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from openharness.agents.remote_tools import DEFAULT_TOOL_NAMES


DEFAULT_HARBOR_VERSION = "0.3.0"
DEFAULT_HARBOR_AGENT_IMPORT_PATH = "openharness.harbor:OpenHarnessHarborAgent"


class HarborExistingJobPolicy(str, Enum):
    """How to handle collisions with an existing Harbor job directory."""

    ERROR = "error"
    RESUME = "resume"
    UNIQUE = "unique"


@dataclass(frozen=True)
class HarborToolSpec:
    """How to provision and invoke the Harbor CLI."""

    version: str = DEFAULT_HARBOR_VERSION
    executable: str = "harbor"
    uv_executable: str = "uv"
    editable_openharness_dir: Path | None = None


@dataclass(frozen=True)
class OpenHarnessHarborAgentSpec:
    """Agent-specific options exposed through the Harbor wrapper."""

    import_path: str = DEFAULT_HARBOR_AGENT_IMPORT_PATH
    model: str | None = None
    remote_cwd: str = "/app"
    tool_names: tuple[str, ...] = DEFAULT_TOOL_NAMES
    max_turns: int = 8
    max_tokens: int = 4096
    system_prompt: str | None = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    def harbor_kwargs(self) -> dict[str, Any]:
        """Return Harbor custom-agent kwargs."""
        kwargs: dict[str, Any] = {
            "remote_cwd": self.remote_cwd,
            "tool_names": list(self.tool_names),
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
        }
        if self.system_prompt is not None:
            kwargs["system_prompt"] = self.system_prompt
        kwargs.update(self.extra_kwargs)
        return kwargs


@dataclass(frozen=True)
class HarborTaskSpec:
    """Select which Harbor task or dataset to execute."""

    path: Path | None = None
    registry_task: str | None = None
    dataset: str | None = None
    task_git_url: str | None = None
    task_git_commit_id: str | None = None
    registry_url: str | None = None
    registry_path: Path | None = None
    include_task_names: tuple[str, ...] = ()
    exclude_task_names: tuple[str, ...] = ()
    n_tasks: int | None = None

    def validate(self) -> None:
        """Ensure exactly one Harbor task source is selected."""
        sources = [
            self.path is not None,
            self.registry_task is not None,
            self.dataset is not None,
            self.task_git_url is not None,
        ]
        if sum(sources) != 1:
            raise ValueError(
                "Exactly one Harbor task source must be set: path, registry_task, dataset, or task_git_url."
            )
        if self.task_git_commit_id is not None and self.task_git_url is None:
            raise ValueError("task_git_commit_id requires task_git_url.")


@dataclass(frozen=True)
class HarborEnvironmentSpec:
    """Execution environment options for a Harbor run."""

    type: str | None = "docker"
    import_path: str | None = None
    force_build: bool | None = None
    delete: bool | None = None
    override_cpus: int | None = None
    override_memory_mb: int | None = None
    override_storage_mb: int | None = None
    override_gpus: int | None = None
    mounts_json: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarborJobSpec:
    """Top-level programmatic configuration for a Harbor job run."""

    task: HarborTaskSpec
    environment: HarborEnvironmentSpec
    agent: OpenHarnessHarborAgentSpec
    jobs_dir: Path
    job_name: str = field(
        default_factory=lambda: datetime.now().strftime("openharness-harbor-%Y%m%d-%H%M%S-%f")
    )
    tool: HarborToolSpec = field(default_factory=HarborToolSpec)
    yes: bool = True
    debug: bool = False
    quiet: bool = False
    n_attempts: int | None = None
    n_concurrent_trials: int | None = 1
    existing_job_policy: HarborExistingJobPolicy = HarborExistingJobPolicy.ERROR
    env_file: Path | None = None
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarborRunResult:
    """Result handle for a Harbor job launched by OpenHarness."""

    command: tuple[str, ...]
    job_name: str
    jobs_dir: Path
    result_path: Path
