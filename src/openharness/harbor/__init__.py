"""Harbor integration for OpenHarness agents."""

from __future__ import annotations

from openharness.harbor.runner import (
    build_harbor_install_command,
    build_harbor_run_command,
    current_harbor_version,
    ensure_harbor_tool,
    resolve_harbor_job_name,
    run_harbor_job,
)
from openharness.harbor.specs import (
    DEFAULT_HARBOR_AGENT_IMPORT_PATH,
    DEFAULT_HARBOR_VERSION,
    HarborEnvironmentSpec,
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborRunResult,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
)
from openharness.workspace.harbor import HarborWorkspace


def __getattr__(name: str):
    if name == "OpenHarnessHarborAgent":
        from openharness.harbor.agent import OpenHarnessHarborAgent

        return OpenHarnessHarborAgent
    raise AttributeError(name)


__all__ = [
    "build_harbor_install_command",
    "build_harbor_run_command",
    "current_harbor_version",
    "DEFAULT_HARBOR_AGENT_IMPORT_PATH",
    "DEFAULT_HARBOR_VERSION",
    "ensure_harbor_tool",
    "HarborEnvironmentSpec",
    "HarborExistingJobPolicy",
    "HarborJobSpec",
    "HarborRunResult",
    "HarborTaskSpec",
    "HarborToolSpec",
    "HarborWorkspace",
    "OpenHarnessHarborAgent",
    "OpenHarnessHarborAgentSpec",
    "resolve_harbor_job_name",
    "run_harbor_job",
]
