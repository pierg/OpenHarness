"""Harbor wrapper around the framework-agnostic OpenHarness agent system."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import logging

from openharness.agents.contracts import TaskDefinition
from openharness.agents.config import AgentConfig
from openharness.agents.factory import AgentFactory
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import MCPServerConfig
from openharness.api.client import SupportsStreamingMessages
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.harbor.trajectory import write_atif
from openharness.observability import create_trace_observer
from openharness.permissions.modes import PermissionMode
from openharness.runtime.session import AgentLogPaths, AgentRuntime
from openharness.runs.context import RunContext
from openharness.tools.base import ToolRegistryFactory
from openharness.workspace.harbor import HarborWorkspace

_log = logging.getLogger(__name__)


OPENHARNESS_HARBOR_VERSION = "0.1.0"


@dataclass(frozen=True)
class HarborRunSummary:
    final_text: str
    input_tokens: int
    output_tokens: int


class OpenHarnessHarborAgent(BaseAgent):
    """Thin Harbor wrapper that delegates to any OpenHarness agent architecture.

    The ``agent_name`` selects which YAML config the factory loads.
    """

    SUPPORTS_ATIF = True

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: Any | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        skills_dir: str | None = None,
        memory_dir: str | None = None,
        *,
        agent_name: str = "basic",
        api_client: SupportsStreamingMessages | None = None,
        extra_env: dict[str, str] | None = None,
        remote_cwd: str = "/app",
        max_turns: int | None = None,
        max_tokens: int | None = None,
        agent_config_yaml: str | None = None,
        run_id: str | None = None,
        run_root: str | Path | None = None,
        tool_registry_factory: ToolRegistryFactory | None = None,
        **kwargs: object,
    ) -> None:
        del kwargs
        # `run_root` is accepted for backwards compatibility but ignored:
        # the agent derives `harbor_job_dir` from Harbor's own trial_dir.
        del run_root
        self._remote_cwd = remote_cwd
        self._extra_env = dict(extra_env or {})
        self._run_id = run_id
        resolved_model_name = (
            model_name
            or self._extra_env.get("OPENHARNESS_MODEL")
            or os.environ.get("OPENHARNESS_MODEL")
        )

        self._agent_name = agent_name
        factory = AgentFactory.with_catalog_configs()
        if agent_config_yaml is not None:
            factory.register(AgentConfig.from_yaml_text(agent_config_yaml, source_name=agent_name))
        config = factory.get_config(agent_name)

        overrides: dict[str, Any] = {}
        if resolved_model_name:
            overrides["model"] = resolved_model_name
        if max_turns is not None:
            overrides["max_turns"] = max_turns
        if max_tokens is not None:
            overrides["max_tokens"] = max_tokens

        if overrides:
            config = config.model_copy(update=overrides)
            factory.register(config)

        self._agent = factory.create(agent_name)

        self._api_client = api_client
        self._tool_registry_factory = tool_registry_factory

        super().__init__(
            logs_dir=logs_dir,
            model_name=resolved_model_name,
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir=skills_dir,
            memory_dir=memory_dir,
        )

    @staticmethod
    def name() -> str:
        return "openharness"

    def version(self) -> str | None:
        return OPENHARNESS_HARBOR_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        with _temporary_environ(self._extra_env):
            settings = load_settings()
            resolved_settings = settings.merge_cli_overrides(model=self.model_name)
            # Harbor trials run unattended in an isolated container with no
            # human in the loop. Force the autonomous system prompt and
            # drop the host-developer personalization sections (CLAUDE.md,
            # local rules, memory) so they don't leak from the host into
            # the trial's prompt.
            resolved_settings = resolved_settings.model_copy(update={"session_mode": "autonomous"})
            resolved_model = self.model_name or resolved_settings.model
            trial_dir = self.logs_dir.parent
            job_run_id = self._run_id or os.environ.get("OPENHARNESS_RUN_ID")
            # The Harbor job directory is the parent of the trial directory:
            # <jobs_dir>/<job_name>/<trial_name>/  →  parent is <jobs_dir>/<job_name>/.
            # This is the real directory Harbor owns, unlike the deprecated
            # `run_root` kwarg which pointed at a synthetic <cwd>/runs/<job>
            # path that no longer exists on disk.
            harbor_job_dir = trial_dir.parent

            # The agent is running in self.logs_dir which is usually trial_dir/agent
            # We want OpenHarness artifacts to live alongside Harbor's trial artifacts
            trial_run_id = trial_dir.name

            run_context = RunContext.from_run_root(
                run_root=trial_dir,
                interface="harbor",
                run_id=trial_run_id,
                cwd=self._remote_cwd,
                metadata={
                    "harbor_logs_dir": str(self.logs_dir),
                    "harbor_job_id": job_run_id,
                    "harbor_job_dir": str(harbor_job_dir),
                },
            )
            messages_path = run_context.artifacts.messages_path
            events_path = run_context.artifacts.events_path
            trace_observer = create_trace_observer(
                session_id=job_run_id if job_run_id else uuid4().hex[:12],
                interface="harbor",
                cwd=self._remote_cwd,
                model=resolved_model,
                provider=detect_provider(resolved_settings).name,
                run_id=run_context.run_id,
            )
            run_context.bind_trace_observer(trace_observer)
            run_context.start(
                metadata={
                    "remote_cwd": self._remote_cwd,
                    "mcp_server_count": len(self.mcp_servers),
                    "harbor_logs_dir": str(self.logs_dir),
                }
            )
            trace_observer.start_session(
                metadata={
                    "remote_cwd": self._remote_cwd,
                    "mcp_server_count": len(self.mcp_servers),
                    "run_dir": str(run_context.run_dir),
                }
            )

            error_message: str | None = None
            summary = HarborRunSummary(final_text="", input_tokens=0, output_tokens=0)
            workspace = HarborWorkspace(environment, cwd=self._remote_cwd)
            log_paths = AgentLogPaths(
                messages_path=str(messages_path),
                events_path=str(events_path),
            )

            runtime = AgentRuntime(
                workspace=workspace,
                settings=resolved_settings,
                permission_mode=PermissionMode.FULL_AUTO,
                api_client=self._api_client,
                log_paths=log_paths,
                trace_observer=trace_observer,
                tool_registry_factory=self._tool_registry_factory,
            )

            try:
                result = await self._agent.run(
                    task=TaskDefinition(instruction=instruction),
                    runtime=runtime,
                )
                summary = HarborRunSummary(
                    final_text=result.final_text,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            except BaseException as exc:
                error_message = str(exc) or type(exc).__name__
                # Preserve partial usage tracked turn-by-turn before the
                # cancellation/error so per-trial telemetry isn't lost on
                # timeouts. ``total_usage`` is updated on every completed
                # API turn inside the conversation loop.
                try:
                    partial = runtime.total_usage
                    summary = HarborRunSummary(
                        final_text=summary.final_text,
                        input_tokens=partial.input_tokens,
                        output_tokens=partial.output_tokens,
                    )
                except Exception:
                    _log.debug("Failed to capture partial usage on agent error", exc_info=True)
                raise
            finally:
                from openharness.observability.cost import estimate_cost

                cost_usd = estimate_cost(
                    model=resolved_model,
                    provider=detect_provider(resolved_settings).name,
                    input_tokens=summary.input_tokens,
                    output_tokens=summary.output_tokens,
                )
                context.n_input_tokens = summary.input_tokens or None
                context.n_output_tokens = summary.output_tokens or None
                context.n_cache_tokens = None
                context.cost_usd = cost_usd
                context.metadata = {
                    "agent_name": self.name(),
                    "agent_version": self.version(),
                    "model": resolved_model,
                    "trace_id": trace_observer.trace_id,
                    "trace_url": trace_observer.trace_url,
                    "run_id": run_context.run_id,
                    "run_root": str(run_context.run_dir),
                    "remote_cwd": self._remote_cwd,
                    "messages_path": str(messages_path),
                    "events_path": str(events_path),
                    "mcp_server_count": len(self.mcp_servers),
                    "summary": asdict(summary),
                    "error": error_message,
                }
                trace_observer.end_session(
                    output={
                        "final_text": summary.final_text,
                        "usage": {
                            "input_tokens": summary.input_tokens,
                            "output_tokens": summary.output_tokens,
                            "total_tokens": summary.input_tokens + summary.output_tokens,
                        },
                    },
                    metadata={
                        "error": error_message,
                        "messages_path": str(messages_path),
                        "events_path": str(events_path),
                    },
                )
                run_context.finish(
                    status="failed" if error_message else "completed",
                    error=error_message,
                    metadata={
                        "agent_name": self.name(),
                        "agent_version": self.version(),
                        "model": resolved_model,
                        "trace_url": trace_observer.trace_url,
                        "remote_cwd": self._remote_cwd,
                    },
                    results={
                        "final_text": summary.final_text,
                    },
                    metrics={
                        "input_tokens": summary.input_tokens,
                        "output_tokens": summary.output_tokens,
                        "total_tokens": summary.input_tokens + summary.output_tokens,
                        "cost_usd": cost_usd,
                    },
                )

                try:
                    trajectory_path = self.logs_dir / "trajectory.json"
                    write_atif(
                        messages_path,
                        trajectory_path,
                        session_id=run_context.run_id,
                        agent_name=self._agent_name,
                        agent_version=self.version() or OPENHARNESS_HARBOR_VERSION,
                        model_name=resolved_model,
                        input_tokens=summary.input_tokens,
                        output_tokens=summary.output_tokens,
                    )
                except Exception:
                    _log.debug("Failed to write ATIF trajectory", exc_info=True)


@contextmanager
def _temporary_environ(overrides: dict[str, str]):
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
